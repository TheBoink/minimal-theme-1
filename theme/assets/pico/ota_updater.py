"""Signed firmware update support for the Pico recovery loader."""
import binascii
import hashlib
import os
import machine
import requests


class UpdateRejectedError(ValueError):
    pass


def sha256(data):
    return hashlib.sha256(data).digest()


def sign(key, data):
    key = key.encode('utf-8')
    if len(key) > 64:
        key = sha256(key)
    key = key + b'\x00' * (64 - len(key))
    inner = bytes(byte ^ 0x36 for byte in key)
    outer = bytes(byte ^ 0x5c for byte in key)
    return binascii.hexlify(sha256(outer + sha256(inner + data))).decode()


def exists(path):
    try:
        os.stat(path)
        return True
    except OSError as error:
        if error.args[0] == 2:
            return False
        raise


def confirm():
    if exists('ota_trial'):
        os.remove('ota_trial')


def check(url, pairing_key, signing_key, feed_watchdog, can_install):
    if not signing_key:
        return
    feed_watchdog()
    response = requests.get(url, headers={'Authorization': 'Bearer ' + pairing_key}, timeout=3)
    try:
        if response.status_code == 204:
            return
        if response.status_code != 200:
            raise OSError('Firmware request failed: %s' % response.status_code)
        release = response.json()
    finally:
        response.close()
        feed_watchdog()
    data = binascii.a2b_base64(release['data'])
    if len(data) > 65536 or sign(signing_key, data) != release['signature']:
        raise UpdateRejectedError('Firmware signature invalid')
    digest = binascii.hexlify(sha256(data)).decode()
    if exists('ota_rejected'):
        with open('ota_rejected', 'r') as file:
            if file.read() == digest:
                return
    with open('pico_app.py', 'rb') as file:
        current = file.read()
    if sha256(current) == sha256(data) or not can_install():
        return
    # Write and close the candidate before changing the current application.
    with open('pico_app.new', 'wb') as file:
        file.write(data)
    if exists('pico_app.bak'):
        os.remove('pico_app.bak')
    with open('ota_trial', 'w') as file:
        file.write('pending')
    os.rename('pico_app.py', 'pico_app.bak')
    os.rename('pico_app.new', 'pico_app.py')
    feed_watchdog()
    machine.reset()
