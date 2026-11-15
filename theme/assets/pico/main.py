"""Pico Post receiver for Pico 2 W + Waveshare Pico-LCD-1.3.
Install the official Waveshare driver as lcd_driver.py alongside this file.
Edit the four placeholders ON YOUR PICO, never in the shared app assets.
"""
import os
import binascii
import gc
import time
import network
import requests
from machine import Pin, PWM
from lcd_driver import LCD_1inch3

WIFI_SSID = 'YOUR_WIFI_NAME'
WIFI_PASSWORD = 'YOUR_WIFI_PASSWORD'
# Copy the complete Device polling endpoint from the app, not its editor URL.
MESSAGE_URL = 'https://YOUR_APP.anvil.app/device/message'
PAIRING_KEY = 'YOUR_PAIRING_KEY'
POLL_SECONDS = 3


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
    response = requests.request(method, url,
                                headers={'Authorization': 'Bearer ' + PAIRING_KEY},
                                json=payload, timeout=10)
    try:
        if response.status_code == 401:
            raise RuntimeError('Pairing key rejected. Check PAIRING_KEY.')
        if response.status_code != 200:
            raise RuntimeError('App HTTP status: ' + str(response.status_code))
        return response.json() if method == 'GET' else None
    finally:
        response.close()


def wifi_indicator(lcd, status):
    # Reserve the right side of the header; never cover message text.
    lcd.fill_rect(140, 6, 100, 16, 0)
    lcd.text('WIFI:' + status, 140, 10, 0xffff)
    lcd.show()


def wait_with_wifi(lcd, wlan, seconds):
    # Refresh the link indicator while a message page is being displayed.
    start = time.ticks_ms()
    previous = None
    while time.ticks_diff(time.ticks_ms(), start) < seconds * 1000:
        status = 'ON' if wlan.isconnected() else 'OFF'
        if status != previous:
            wifi_indicator(lcd, status)
            previous = status
        time.sleep_ms(250)


def draw(lcd, lines, page, total, wifi_status='WAIT', app_status=None):
    lcd.fill(0)
    lcd.text('PICO POST', 12, 10, 0xffff)
    lcd.hline(12, 25, 216, 0xffff)
    for index, line in enumerate(lines[page * 23:(page + 1) * 23]):
        lcd.text(line, 12, 36 + index * 8, 0xffff)
    lcd.text('%d / %d' % (page + 1, total), 12, 226, 0xffff)
    if app_status:
        lcd.text(app_status, 100, 226, 0xffff)
    wifi_indicator(lcd, wifi_status)


def connect(wlan, on_status):
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
        time.sleep_ms(250)
    on_status('ON')


class ButtonA:
    """Capture falling edges even while Wi-Fi/HTTP is blocking."""
    def __init__(self):
        self.total = 0
        self.last_press = time.ticks_ms()
        self.pin = Pin(15, Pin.IN, Pin.PULL_UP)
        self.pin.irq(trigger=Pin.IRQ_FALLING, handler=self.pressed)

    def pressed(self, pin):
        now = time.ticks_ms()
        if time.ticks_diff(now, self.last_press) >= 200:
            self.last_press = now
            self.total += 1


def main():
    if PAIRING_KEY == 'YOUR_PAIRING_KEY' or WIFI_SSID == 'YOUR_WIFI_NAME':
        raise RuntimeError('Fill in the four configuration values in main.py on your Pico.')
    if not MESSAGE_URL.startswith('https://') or not MESSAGE_URL.endswith('/device/message'):
        raise RuntimeError('Copy the full HTTPS Device polling endpoint from the app.')
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
    draw(lcd, ['Connecting to Wi-Fi...'], 0, 1)
    wlan = network.WLAN(network.STA_IF)
    current_revision = None
    acknowledged_revision = None
    lines = ['Waiting for a message...']
    page = 0
    pages = 1
    while True:
        try:
            connect(wlan, lambda status: wifi_indicator(lcd, status))
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
            print('Network unavailable; retrying in 3 seconds.')
            draw(lcd, lines, page, pages, 'ON' if wlan.isconnected() else 'OFF', 'APP: RETRY')
            wait_with_wifi(lcd, wlan, POLL_SECONDS)
            gc.collect()
            continue
        revision = data['revision']
        if revision != current_revision:
            lines = wrap_text(data['text']) if revision else ['Waiting for a message...']
            current_revision = revision
            page = 0
        pages = max(1, (len(lines) + 22) // 23)
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
