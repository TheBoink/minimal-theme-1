"""Pico Post receiver for Pico 2 W + Waveshare Pico-LCD-1.3.
Install the official Waveshare driver as lcd_driver.py alongside this file.
Create pico_config.py on your Pico using the supplied template.
"""
import os
import binascii
import framebuf
import gc
import time
import ntptime
import network
import requests
from machine import Pin, PWM, WDT, Timer
from lcd_driver import LCD_1inch3

# pico_config.py lives on your Pico, next to main.py and lcd_driver.py.
from pico_config import (WIFI_SSID, WIFI_PASSWORD, MESSAGE_URL, PAIRING_KEY,
                         POLL_SECONDS, WATCHDOG_TIMEOUT_MS, HTTP_TIMEOUT_SECONDS)

watchdog = None
started_at = time.ticks_ms()
activity = None


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


def wrap_text(text, width=27):
    """Preserve newlines, wrap words, and split words wider than the LCD."""
    lines = []
    for paragraph in text.split('\n'):
        while len(paragraph) > width:
            split = paragraph.rfind(' ', 0, width + 1)
            if split <= 0:
                split = width
            lines.append(paragraph[:split])
            paragraph = paragraph[split:]
            if paragraph.startswith(' '):
                paragraph = paragraph[1:]
        lines.append(paragraph)
    return lines


def fetch(method, url, payload=None):
    feed_watchdog()
    response = requests.request(method, url,
                                headers={'Authorization': 'Bearer ' + PAIRING_KEY},
                                json=payload, timeout=HTTP_TIMEOUT_SECONDS)
    try:
        if response.status_code == 401:
            raise timestamped_error('Pairing key rejected. Check PAIRING_KEY.')
        if response.status_code != 200:
            raise timestamped_error('App HTTP status: ' + str(response.status_code))
        return response.json() if method == 'GET' else None
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
            return 'UTC --:--:-- (syncing)'
        now = time.gmtime(time.time() + self.utc_offset)
        return '%s %02d:%02d:%02d' % (self.timezone, now[3], now[4], now[5])


clock = DisplayClock()


def clock_indicator(lcd):
    lcd.fill_rect(12, 28, 216, 10, 0)
    lcd.text(clock.label(), 12, 28, 0xffff)


def wifi_indicator(lcd, status):
    # Reserve the right side of the header; never cover message text.
    lcd.fill_rect(140, 6, 100, 16, 0)
    lcd.text('WIFI:' + status, 140, 10, 0xffff)
    clock_indicator(lcd)
    lcd.show()


def wait_with_wifi(lcd, wlan, seconds):
    # Refresh the link indicator while a message page is being displayed.
    start = time.ticks_ms()
    previous = None
    while time.ticks_diff(time.ticks_ms(), start) < seconds * 1000:
        status = 'ON' if wlan.isconnected() else 'OFF'
        state = (status, clock.label())
        if state != previous:
            wifi_indicator(lcd, status)
            previous = state
        feed_watchdog()
        time.sleep_ms(250)


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


def activity_color(count):
    """RGB565 byte order for the Waveshare framebuffer sent directly over SPI."""
    if count >= 20:
        red, green, blue = 255, 0, 0
    elif count >= 10:
        red, green, blue = 255, 165, 0
    elif count >= 1:
        red, green, blue = 255, 255, 0
    else:
        red, green, blue = 144, 238, 144
    rgb565 = ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)
    return ((rgb565 & 255) << 8) | (rgb565 >> 8)


def draw_activity(lcd):
    """Oldest day first, today last; each with 12 two-hour boxes."""
    if activity is None:
        lcd.text('Activity: syncing...', 4, 44, 0xffff)
        return
    for row, day in enumerate(activity.get('days', [activity])[::-1]):
        y = 42 + row * 20
        large_text(lcd, day['date'], 4, y)
        counts = day.get('counts')
        for index, occupied in enumerate(day['slots']):
            x = 104 + index * 11
            count = counts[index] if counts is not None else int(occupied)
            lcd.fill_rect(x, y, 9, 16, activity_color(count))


def draw(lcd, lines, page, total, wifi_status='WAIT', app_status=None):
    lcd.fill(0)
    lcd.text('PICO POST', 12, 10, 0xffff)
    lcd.hline(12, 25, 216, 0xffff)
    draw_activity(lcd)
    for index, line in enumerate(lines[page * 15:(page + 1) * 15]):
        lcd.text(line, 12, 102 + index * 8, 0xffff)
    lcd.text('%d / %d' % (page + 1, total), 12, 226, 0xffff)
    if app_status:
        lcd.text(app_status, 100, 226, 0xffff)
    wifi_indicator(lcd, wifi_status)


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
    def __init__(self):
        self.total = 0
        # External switch: physical pin 19 (GP14) to physical pin 18 (GND).
        self.pin = Pin(14, Pin.IN, Pin.PULL_UP)
        self.stable = self.pin.value()
        self.candidate = self.stable
        self.samples = 0
        # Hard callback continues during blocking network requests. It only uses
        # pre-existing objects and small integers, so it does not allocate.
        self.timer = Timer(-1)
        self.timer.init(period=10, mode=Timer.PERIODIC, callback=self.sample, hard=True)

    def sample(self, timer):
        value = self.pin.value()
        if value != self.candidate:
            self.candidate = value
            self.samples = 1
        elif self.samples < 10:
            self.samples += 1
        if self.samples == 10 and self.candidate != self.stable:
            self.stable = self.candidate
            if self.stable == 0:
                self.total += 1


def main():
    global watchdog, activity
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
    print('Starting watchdog in 5 seconds; Ctrl+C to stop for maintenance.')
    time.sleep(5)
    watchdog = WDT(timeout=WATCHDOG_TIMEOUT_MS)
    feed_watchdog()
    ntptime.timeout = 1
    ack_url = MESSAGE_URL[:-len('/message')] + '/ack'
    button_url = MESSAGE_URL[:-len('/message')] + '/button'
    button = ButtonA()
    boot_id = binascii.hexlify(os.urandom(8)).decode()
    sent_presses = 0
    pending_event = None
    pending_total = 0
    backlight = PWM(Pin(13))
    backlight.freq(1000)
    backlight.duty_u16(32768)
    lcd = LCD_1inch3()
    feed_watchdog()
    draw(lcd, ['Connecting to Wi-Fi...'], 0, 1)
    wlan = network.WLAN(network.STA_IF)
    current_revision = None
    acknowledged_revision = None
    lines = ['Waiting for a message...']
    page = 0
    pages = 1
    while True:
        feed_watchdog()
        try:
            connect(wlan, lambda status: wifi_indicator(lcd, status))
            clock.sync()
            wifi_indicator(lcd, 'ON')
            if pending_event is None and button.total > sent_presses:
                pending_total = sent_presses + 1
                pending_event = {'button': 'A', 'count': pending_total - sent_presses,
                                 'event_id': boot_id + '-' + str(pending_total)}
            if pending_event is not None:
                fetch('POST', button_url, pending_event)
                sent_presses = pending_total
                pending_event = None
            data = fetch('GET', MESSAGE_URL)
        except OSError:
            # Network outages are expected; preserve the last message and retry.
            print('Network unavailable; retrying in %s seconds.' % POLL_SECONDS)
            draw(lcd, lines, page, pages, 'ON' if wlan.isconnected() else 'OFF', 'APP: RETRY')
            wait_with_wifi(lcd, wlan, POLL_SECONDS)
            gc.collect()
            continue
        clock.utc_offset = data.get('utc_offset', 0)
        clock.timezone = data.get('timezone', 'UTC')
        activity = data.get('activity')
        revision = data['revision']
        if revision != current_revision:
            lines = wrap_text(data['text']) if revision else ['Waiting for a message...']
            current_revision = revision
            page = 0
        pages = max(1, (len(lines) + 14) // 15)
        draw(lcd, lines, page, pages, 'ON' if wlan.isconnected() else 'OFF')
        # Let the final page be visible before confirming delivery.
        wait_with_wifi(lcd, wlan, POLL_SECONDS)
        if page == pages - 1 and acknowledged_revision != revision:
            try:
                fetch('POST', ack_url, {'revision': revision})
            except OSError:
                print('Acknowledgement interrupted; will retry.')
                draw(lcd, lines, page, pages, 'ON' if wlan.isconnected() else 'OFF', 'APP: RETRY')
            else:
                acknowledged_revision = revision
        page = (page + 1) % pages
        gc.collect()


if __name__ == '__main__':
    main()
