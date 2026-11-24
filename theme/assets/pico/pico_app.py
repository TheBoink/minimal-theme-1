"""Pico Post receiver for Pico 2 W + Waveshare Pico-LCD-1.3.
Install the official Waveshare driver as lcd_driver.py alongside this file.
Create pico_config.py on your Pico using the supplied template.
"""
import os
import binascii
import framebuf
import gc
import hashlib
import time
import ntptime
import network
import requests
import ota_updater
import pico_config
from machine import ADC, Pin, PWM, WDT, Timer
from lcd_driver import LCD_1inch3

# pico_config.py lives on your Pico, next to main.py and lcd_driver.py.
from pico_config import (WIFI_SSID, WIFI_PASSWORD, MESSAGE_URL, PAIRING_KEY,
                         POLL_SECONDS, WATCHDOG_TIMEOUT_MS, HTTP_TIMEOUT_SECONDS)

def firmware_version():
    digest = hashlib.sha256()
    with open('pico_app.py', 'rb') as firmware:
        while True:
            chunk = firmware.read(1024)
            if not chunk:
                break
            digest.update(chunk)
    return binascii.hexlify(digest.digest()).decode()[:12]


FIRMWARE_VERSION = firmware_version()
watchdog = None
started_at = time.ticks_ms()
activity = None
screen_button = None
screen_last_count = 0
screen_last_press = None
screensaver_active = False
screen_last_draw = None
screen_notice_seen = 0
screen_notice_visible = False
battery_label = "BAT --"


def timestamped_error(message):
    if clock.synced:
        now = time.gmtime()
        stamp = '%04d-%02d-%02d %02d:%02d:%02d UTC' % now[:6]
    else:
        elapsed = time.ticks_diff(time.ticks_ms(), started_at) // 1000
        stamp = 'UTC not synced; uptime %ds' % elapsed
    return RuntimeError('[' + stamp + '] ' + message)


def feed_watchdog():
    # Never feed from an IRQ or background timer: stalled code must reset.
    if watchdog is not None:
        watchdog.feed()


def fetch(method, url, payload=None, expect_json=False):
    feed_watchdog()
    response = requests.request(method, url,
                                headers={'Authorization': 'Bearer ' + PAIRING_KEY,
                                         'X-Firmware-Version': FIRMWARE_VERSION},
                                json=payload, timeout=HTTP_TIMEOUT_SECONDS)
    try:
        if response.status_code == 401:
            raise timestamped_error('Pairing key rejected. Check PAIRING_KEY.')
        if response.status_code >= 500:
            raise OSError('App HTTP status: ' + str(response.status_code))
        if response.status_code != 200:
            raise timestamped_error('App HTTP status: ' + str(response.status_code))
        return response.json() if method == 'GET' or expect_json else None
    finally:
        response.close()
        feed_watchdog()


class DisplayClock:
    def __init__(self):
        self.synced = False
        self.utc_offset = 0
        self.timezone = 'UTC'
        self.next_sync = None

    def sync(self):
        now = time.ticks_ms()
        if self.next_sync is not None and time.ticks_diff(now, self.next_sync) < 0:
            return
        feed_watchdog()
        try:
            ntptime.settime()
        except OSError:
            # NTP may be blocked or temporarily unavailable; keep app delivery working.
            self.next_sync = time.ticks_add(time.ticks_ms(), 60000)
            print('Clock sync unavailable; retrying in one minute.')
        else:
            self.synced = True
            self.next_sync = time.ticks_add(time.ticks_ms(), 3600000)
        feed_watchdog()

    def label(self):
        if not self.synced:
            return 'UTC --:--:--'
        now = time.gmtime(time.time() + self.utc_offset)
        return '%s %02d:%02d:%02d' % (self.timezone, now[3], now[4], now[5])


clock = DisplayClock()


def clock_indicator(lcd):
    lcd.fill_rect(0, 202, 240, 18, 0)
    time_text = clock.label().split(' ')[-1]
    large_text(lcd, time_text, 0, 203)
    battery_text = battery_label.replace('BAT ', '').replace('USB POWER', 'USB')
    large_text(lcd, battery_text, 240 - len(battery_text) * 16, 203)


def wifi_indicator(lcd, status):
    update_screensaver(lcd)
    if screensaver_active:
        return
    # Wi-Fi arcs in the top-right corner, alongside the title.
    lcd.fill_rect(196, 2, 44, 20, 0)
    color = 0xffff
    lcd.line(200, 7, 206, 3, color)
    lcd.line(206, 3, 216, 3, color)
    lcd.line(216, 3, 222, 7, color)
    lcd.line(204, 11, 208, 8, color)
    lcd.line(208, 8, 214, 8, color)
    lcd.line(214, 8, 218, 11, color)
    lcd.line(208, 14, 211, 12, color)
    lcd.line(211, 12, 214, 14, color)
    lcd.fill_rect(210, 17, 3, 3, color)
    if status == 'OFF':
        lcd.line(200, 19, 222, 2, 0x00f8)
        lcd.line(201, 19, 223, 2, 0x00f8)
    elif status != 'ON':
        lcd.text('?', 229, 8, color)
    clock_indicator(lcd)
    lcd.show()


def wait_with_wifi(lcd, wlan, seconds):
    # Refresh the link indicator while a message page is being displayed.
    start = time.ticks_ms()
    previous = None
    while time.ticks_diff(time.ticks_ms(), start) < seconds * 1000:
        update_screensaver(lcd)
        if screen_button is not None and screen_button.events:
            return
        status = 'ON' if wlan.isconnected() else 'OFF'
        state = (status, clock.label())
        if state != previous:
            wifi_indicator(lcd, status)
            previous = state
        feed_watchdog()
        time.sleep_ms(250)


DEFAULT_CHECKING_MESSAGE = 'One moment please...'
DEFAULT_WAITING_MESSAGE = 'Even geduld voor de volgende borrel (nog {minutes} minuten).'


def wrap_waiting_message(text):
    lines = []
    for paragraph in text.split('\n'):
        while len(paragraph) > 15:
            split = paragraph.rfind(' ', 0, 16)
            if split <= 0:
                split = 15
            lines.append(paragraph[:split])
            paragraph = paragraph[split:].lstrip(' ')
        lines.append(paragraph)
    return lines


def large_text(lcd, text, x, y):
    """Render the built-in 8x8 font at double size using a tiny glyph buffer."""
    glyph = framebuf.FrameBuffer(bytearray(8), 8, 8, framebuf.MONO_HLSB)
    for index, character in enumerate(text):
        glyph.fill(0)
        glyph.text(character, 0, 0, 1)
        for gy in range(8):
            for gx in range(8):
                if glyph.pixel(gx, gy):
                    lcd.fill_rect(x + index * 16 + gx * 2, y + gy * 2, 2, 2, 0xffff)


def render_logo(lcd):
    """Draw the optional RGB565 image, or the built-in wordmark."""
    try:
        logo = open('startup_logo.rgb565', 'rb')
    except OSError as error:
        if error.errno != 2:
            raise
        # Keep a useful startup display when the optional image is not installed.
        lcd.fill(0)
        lcd.rect(36, 56, 168, 115, 0xffff)
        lcd.fill_rect(105, 75, 30, 6, 0xffff)
        lcd.fill_rect(117, 63, 6, 30, 0xffff)
        large_text(lcd, 'Drink Buddy', 32, 108)
        lcd.text('CONNECTING YOUR WORLD', 36, 147, 0xffff)
    else:
        try:
            view = memoryview(lcd.buffer)
            offset = 0
            while offset < len(view):
                amount = logo.readinto(view[offset:offset + 4096])
                if not amount:
                    raise ValueError('startup_logo.rgb565 must contain 115200 bytes')
                offset += amount
                feed_watchdog()
            if logo.read(1):
                raise ValueError('startup_logo.rgb565 must contain 115200 bytes')
        finally:
            logo.close()
    lcd.show()


def show_startup_logo(lcd):
    render_logo(lcd)
    start = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), start) < 10000:
        feed_watchdog()
        time.sleep_ms(100)


def update_screensaver(lcd):
    global screen_last_count, screen_last_press, screensaver_active
    global screen_notice_seen, screen_notice_visible
    if screen_button is None:
        return
    now = time.ticks_ms()
    if screen_button.total != screen_last_count:
        screen_last_count = screen_button.total
        screen_last_press = now
        if screensaver_active:
            screensaver_active = False
            if screen_last_draw is not None:
                draw(lcd, *screen_last_draw)
    elif not screensaver_active and time.ticks_diff(now, screen_last_press) >= 30000:
        screensaver_active = True
        render_logo(lcd)
    notice_active = (screen_button.notice_until is not None and
                     time.ticks_diff(screen_button.notice_until, now) > 0)
    if (screen_button.notice_version != screen_notice_seen or
            notice_active != screen_notice_visible):
        screen_notice_seen = screen_button.notice_version
        screen_notice_visible = notice_active
        if screen_last_draw is not None and not screensaver_active:
            draw(lcd, *screen_last_draw)


def activity_color(count):
    """RGB565 byte order for the Waveshare framebuffer sent directly over SPI."""
    if count >= 4:
        red, green, blue = 255, 0, 0
    elif count >= 2:
        red, green, blue = 255, 165, 0
    elif count >= 1:
        red, green, blue = 255, 255, 0
    else:
        red, green, blue = 144, 238, 144
    rgb565 = ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)
    return ((rgb565 & 255) << 8) | (rgb565 >> 8)


def draw_activity(lcd):
    """Yesterday first, today last; each with 12 two-hour boxes."""
    if activity is None:
        lcd.text('Activity: syncing...', 4, 50, 0xffff)
        return
    for row, day in enumerate(activity.get('days', [activity])[:2][::-1]):
        y = 48 + row * 20
        large_text(lcd, day['date'], 4, y)
        counts = day.get('counts')
        for index, occupied in enumerate(day['slots']):
            x = 104 + index * 11
            count = counts[index] if counts is not None else int(occupied)
            lcd.fill_rect(x, y, 9, 16, activity_color(count))


def draw(lcd, wifi_status='WAIT', app_status=None):
    global screen_last_draw
    screen_last_draw = (wifi_status, app_status)
    update_screensaver(lcd)
    if screensaver_active:
        return
    lcd.fill(0)
    large_text(lcd, 'Drink Buddy', 8, 4)
    lcd.hline(8, 44, 224, 0xffff)
    lcd.hline(8, 199, 224, 0xffff)
    draw_activity(lcd)
    lcd.hline(8, 86, 224, 0xffff)
    lcd.text('FW ' + FIRMWARE_VERSION, 8, 28, 0xffff)
    if app_status:
        lcd.text(app_status, 8, 224, 0xffff)
    if screen_button is not None and screen_notice_visible:
        lcd.fill_rect(0, 108, 240, 90, 0)
        message = screen_button.notice_text
        for index, line in enumerate(wrap_waiting_message(message)):
            large_text(lcd, line, (240 - len(line) * 16) // 2, 109 + index * 17)
    wifi_indicator(lcd, wifi_status)


def read_battery(wlan):
    """Temporarily stop Wi-Fi to measure the shared VSYS input."""
    if Pin('WL_GPIO2', Pin.IN).value():
        return 'USB POWER'
    wlan.active(False)
    try:
        Pin(25, Pin.OUT, Pin.PULL_DOWN).value(1)
        Pin(29, Pin.IN)
        voltage = ADC(3).read_u16() * (3 * 3.3 / 65535)
    finally:
        Pin(29, Pin.ALT, Pin.PULL_DOWN, alt=7)
        wlan.active(True)
    if voltage < 2.0 or voltage > 4.5:
        return 'BAT --'
    percentage = max(0, min(100, int((voltage - 2.8) * 100 / (4.2 - 2.8))))
    return 'BAT ~%d%%' % percentage


def connect(wlan, on_status):
    feed_watchdog()
    if wlan.isconnected():
        return
    on_status('JOIN')
    wlan.active(True)
    wlan.disconnect()
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)
    start = time.ticks_ms()
    while not wlan.isconnected():
        if wlan.status() < 0 or time.ticks_diff(time.ticks_ms(), start) > 20000:
            raise OSError('Wi-Fi connection failed')
        on_status('JOIN')
        feed_watchdog()
        time.sleep_ms(250)
    on_status('ON')


class ButtonA:
    """Debounce press and release with fixed-interval GPIO sampling."""
    def __init__(self, pulse_output):
        self.total = 0
        # Queue per-press outcomes until the server acknowledges them.
        self.events = []
        self.ready = False
        self.checking_message = DEFAULT_CHECKING_MESSAGE
        self.pulse_output = pulse_output
        self.pulse_active = False
        self.pulse_gap_ms = 500
        self.cooldown_minutes = 0
        self.waiting_message = DEFAULT_WAITING_MESSAGE
        self.last_action_tick = None
        self.notice_until = None
        self.notice_text = ''
        self.notice_version = 0
        # External switch: physical pin 19 (GP14) to physical pin 18 (GND).
        self.pin = Pin(14, Pin.IN, Pin.PULL_UP)
        self.stable = self.pin.value()
        self.candidate = self.stable
        self.candidate_since = time.ticks_ms()
        # The default soft timer works across Pico MicroPython versions.
        self.door_pin = Pin(27, Pin.IN, Pin.PULL_UP)
        self.door_open_level = getattr(pico_config, 'DOOR_OPEN_LEVEL', 0)
        self.door_stable = self.door_candidate = self.door_pin.value()
        self.door_since = time.ticks_ms()
        self.door_events = []
        self.door_sequence = 0
        self.tilt_pin = Pin(26, Pin.IN, Pin.PULL_DOWN)
        self.tilt_active_level = getattr(pico_config, 'TILT_ACTIVE_LEVEL', 1)
        self.tilt_stable = self.tilt_candidate = self.tilt_pin.value()
        self.tilt_since = time.ticks_ms()
        self.tilt_events = []
        self.tilt_sequence = 0
        self.unix_offset = 946684800 if time.gmtime(0)[0] == 2000 else 0
        self.timer = Timer(-1)
        self.timer.init(period=10, mode=Timer.PERIODIC, callback=self.sample)

    def sample(self, timer):
        value = self.pin.value()
        now = time.ticks_ms()
        door_level = self.door_pin.value()
        if door_level != self.door_candidate:
            self.door_candidate = door_level
            self.door_since = now
        if door_level != self.door_stable and time.ticks_diff(now, self.door_since) >= 100:
            self.door_stable = door_level
            if door_level == self.door_open_level:
                self.door_sequence += 1
                opened_at = int(time.time()) + self.unix_offset if clock.synced else None
                self.door_events.append((self.door_sequence, opened_at))
        tilt_level = self.tilt_pin.value()
        if tilt_level != self.tilt_candidate:
            self.tilt_candidate = tilt_level
            self.tilt_since = now
        if tilt_level != self.tilt_stable and time.ticks_diff(now, self.tilt_since) >= 100:
            self.tilt_stable = tilt_level
            if tilt_level == self.tilt_active_level:
                self.tilt_sequence += 1
                tilted_at = int(time.time()) + self.unix_offset if clock.synced else None
                self.tilt_events.append((self.tilt_sequence, tilted_at))
        # Drop expired action times before the tick counter can wrap ambiguously.
        if (self.last_action_tick is not None and
                time.ticks_diff(now, self.last_action_tick) >= 86400000):
            self.last_action_tick = None
        if value != self.candidate:
            self.candidate = value
            self.candidate_since = now
        if (self.candidate != self.stable
                and time.ticks_diff(now, self.candidate_since) >= 100):
            self.stable = self.candidate
            if self.stable == 0:
                self.total += 1
                event_number = self.total
                if self.ready:
                    self.events.append(event_number)
                else:
                    self.notice('Wi-Fi sync needed.', now)

    def notice(self, message, now):
        self.notice_text = message
        self.notice_until = time.ticks_add(now, 20000)
        self.notice_version += 1

    def dispense(self, gap_ms, now):
        self.last_action_tick = now
        # GP22 is open drain: 0 sinks to ground; 1 releases the line.
        self.pulse_active = True
        try:
            for pulse in range(2):
                self.pulse_output.value(0)
                try:
                    time.sleep_ms(100)
                finally:
                    self.pulse_output.value(1)
                if pulse == 0:
                    remaining = gap_ms
                    while remaining > 0:
                        step = min(remaining, 100)
                        time.sleep_ms(step)
                        remaining -= step
                        feed_watchdog()
        finally:
            self.pulse_active = False


def main():
    global watchdog, activity, screen_button, screen_last_count, screen_last_press, battery_label
    if (not isinstance(WIFI_SSID, str) or not WIFI_SSID or WIFI_SSID == 'YOUR_WIFI_NAME'
            or not isinstance(WIFI_PASSWORD, str)
            or not isinstance(PAIRING_KEY, str) or not PAIRING_KEY
            or PAIRING_KEY == 'YOUR_PAIRING_KEY'):
        raise timestamped_error('Set Wi-Fi and pairing values in pico_config.py on your Pico.')
    if not isinstance(MESSAGE_URL, str) or not MESSAGE_URL.startswith('https://') or not MESSAGE_URL.endswith('/device/message'):
        raise timestamped_error('Set MESSAGE_URL to the HTTPS Device polling endpoint in pico_config.py.')
    if (type(POLL_SECONDS) is not int or POLL_SECONDS < 1
            or type(WATCHDOG_TIMEOUT_MS) is not int or not 1000 <= WATCHDOG_TIMEOUT_MS <= 8000
            or type(HTTP_TIMEOUT_SECONDS) is not int or HTTP_TIMEOUT_SECONDS < 1
            or HTTP_TIMEOUT_SECONDS * 1000 >= WATCHDOG_TIMEOUT_MS):
        raise timestamped_error('Check the timing settings in pico_config.py.')
    # Release GP22 before the startup delay, so an active-low interface is off.
    pulse_output = Pin(22, Pin.OPEN_DRAIN, value=1)
    tilt_debug = getattr(pico_config, 'TILT_DEBUG', False)
    if tilt_debug:
        print('Tilt debug enabled. Watchdog is off.')
    else:
        print('Starting watchdog in 5 seconds; Ctrl+C to stop for maintenance.')
        time.sleep(5)
        watchdog = WDT(timeout=WATCHDOG_TIMEOUT_MS)
    feed_watchdog()
    ntptime.timeout = 1
    button_url = MESSAGE_URL[:-len('/message')] + '/button'
    firmware_url = MESSAGE_URL[:-len('/message')] + '/firmware'
    tilt_url = MESSAGE_URL[:-len('/message')] + '/tilt'
    door_url = MESSAGE_URL[:-len('/message')] + '/door'
    battery_url = MESSAGE_URL[:-len('/message')] + '/battery'
    ota_key = getattr(pico_config, 'OTA_SIGNING_KEY', '')
    next_update_check = time.ticks_ms()
    button = ButtonA(pulse_output)
    boot_id = binascii.hexlify(os.urandom(8)).decode()
    pending_event = None
    pending_battery = None
    battery_sequence = 0
    backlight = PWM(Pin(13))
    backlight.freq(1000)
    backlight.duty_u16(32768)
    lcd = LCD_1inch3()
    feed_watchdog()
    show_startup_logo(lcd)
    screen_button = button
    screen_last_count = button.total
    screen_last_press = time.ticks_ms()
    draw(lcd)
    wlan = network.WLAN(network.STA_IF)
    next_battery_check = time.ticks_ms()
    last_tilt_state = None
    while True:
        if tilt_debug:
            state = (button.tilt_candidate, button.tilt_stable, button.tilt_sequence)
            if state != last_tilt_state:
                print('GP26 raw=%d stable=%d detected=%d active=%d' % (state + (button.tilt_active_level,)))
                last_tilt_state = state
        feed_watchdog()
        checking_press = False
        try:
            connect(wlan, lambda status: wifi_indicator(lcd, status))
            if time.ticks_diff(time.ticks_ms(), next_battery_check) >= 0:
                next_battery_check = time.ticks_add(time.ticks_ms(), 3600000)
                battery_label = read_battery(wlan)
                battery_sequence += 1
                battery_status = 'usb' if battery_label == 'USB POWER' else 'unavailable' if battery_label == 'BAT --' else 'battery'
                battery_percentage = int(battery_label[5:-1]) if battery_status == 'battery' else None
                pending_battery = {'event_id': boot_id + '-battery-' + str(battery_sequence), 'status': battery_status, 'percentage': battery_percentage}
                connect(wlan, lambda status: wifi_indicator(lcd, status))
            clock.sync()
            wifi_indicator(lcd, 'ON')
            if pending_event is not None:
                fetch('POST', button_url, pending_event)
                pending_event = None
            if button.events:
                number = button.events.pop(0)
                gap = button.pulse_gap_ms
                event = {'button': 'A', 'count': 1, 'event_id': boot_id + '-' + str(number),
                         'pulse_gap_ms': gap}
                checking_press = True
                button.notice(button.checking_message, time.ticks_ms())
                update_screensaver(lcd)
                decision = fetch('POST', button_url, dict(event, authorize=True), expect_json=True)
                if decision['allowed'] is True:
                    button.dispense(gap, time.ticks_ms())
                    pending_event = dict(event, drink_triggered=True)
                    if decision['message']:
                        button.notice(decision['message'], time.ticks_ms())
                    else:
                        button.notice_until = None
                else:
                    button.notice(decision['message'], time.ticks_ms())
                checking_press = False
                update_screensaver(lcd)
            if pending_battery is not None:
                fetch('POST', battery_url, pending_battery)
                pending_battery = None
            if button.door_events:
                sequence, opened_at = button.door_events[0]
                fetch('POST', door_url, {'event_id': boot_id + '-door-' + str(sequence),
                                         'opened_at': opened_at})
                button.door_events.pop(0)
            if button.tilt_events:
                sequence, tilted_at = button.tilt_events[0]
                fetch('POST', tilt_url, {'event_id': boot_id + '-tilt-' + str(sequence),
                                         'tilted_at': tilted_at})
                button.tilt_events.pop(0)
                if tilt_debug:
                    print('Tilt %d acknowledged by Anvil' % sequence)
            data = fetch('GET', MESSAGE_URL)
        except OSError:
            button.ready = False
            if checking_press or button.events or button.pin.value() == 0:
                button.notice('Wi-Fi sync needed.', time.ticks_ms())
            button.events.clear()
            # Network outages are expected; preserve the last message and retry.
            print('Network unavailable; retrying in %s seconds.' % POLL_SECONDS)
            draw(lcd, 'ON' if wlan.isconnected() else 'OFF', 'APP: RETRY')
            wait_with_wifi(lcd, wlan, POLL_SECONDS)
            gc.collect()
            continue
        # A first successful poll proves the new firmware can start and connect.
        ota_updater.confirm()
        if (ota_key and not button.tilt_events and not button.door_events and not button.events and not button.pulse_active and pending_event is None and
                (button.last_action_tick is None or
                 time.ticks_diff(time.ticks_ms(), button.last_action_tick) >= button.cooldown_minutes * 60000) and
                time.ticks_diff(time.ticks_ms(), next_update_check) >= 0):
            next_update_check = time.ticks_add(time.ticks_ms(), 60000)
            try:
                ota_updater.check(firmware_url, PAIRING_KEY, ota_key, feed_watchdog,
                                  lambda: not button.tilt_events and not button.door_events and not button.events and not button.pulse_active and button.stable == 1 and
                                  (button.last_action_tick is None or
                                   time.ticks_diff(time.ticks_ms(), button.last_action_tick) >= button.cooldown_minutes * 60000))
            except OSError:
                print('Firmware update temporarily unavailable; retrying later.')
            except ota_updater.UpdateRejectedError:
                print('Firmware update rejected: check the update signing key.')
        gap = data.get('pulse_gap_ms', 500)
        if type(gap) is not int or not 0 <= gap <= 5000:
            raise timestamped_error('Invalid pulse delay received from the app.')
        button.pulse_gap_ms = gap
        cooldown = data.get('cooldown_minutes', 0)
        if type(cooldown) is not int or not 0 <= cooldown <= 1440:
            raise timestamped_error('Invalid waiting period received from the app.')
        button.cooldown_minutes = cooldown
        message = data.get('waiting_message', DEFAULT_WAITING_MESSAGE)
        if (not isinstance(message, str) or message.count('{minutes}') != 1 or
                any(len(wrap_waiting_message(message.replace('{minutes}', n))) > 5 for n in ('1', '10', '100', '1440'))):
            raise timestamped_error('Invalid waiting message received from the app.')
        button.waiting_message = message
        button.checking_message = data.get('drink_checking_message', DEFAULT_CHECKING_MESSAGE)
        button.ready = True
        clock.utc_offset = data.get('utc_offset', 0)
        clock.timezone = data.get('timezone', 'UTC')
        activity = data.get('activity')
        draw(lcd, 'ON' if wlan.isconnected() else 'OFF')
        wait_with_wifi(lcd, wlan, POLL_SECONDS)
        gc.collect()


if __name__ == '__main__':
    main()
