"""Pico Post recovery loader. Install once as main.py on the Pico."""
import os
import binascii
import hashlib
import machine


def exists(path):
    try:
        os.stat(path)
        return True
    except OSError as error:
        if error.args[0] == 2:
            return False
        raise


def remove(path):
    if exists(path):
        os.remove(path)


def recover():
    if not exists('ota_trial'):
        if not exists('pico_app.py') and exists('pico_app.bak'):
            os.rename('pico_app.bak', 'pico_app.py')
        return
    with open('ota_trial', 'r') as file:
        state = file.read()
    if state == 'pending' and exists('pico_app.py') and exists('pico_app.bak') and not exists('pico_app.new'):
        with open('ota_trial', 'w') as file:
            file.write('running')
        return
    if state == 'running' and exists('pico_app.py'):
        with open('pico_app.py', 'rb') as file:
            digest = binascii.hexlify(hashlib.sha256(file.read()).digest()).decode()
        with open('ota_rejected', 'w') as file:
            file.write(digest)
    if exists('pico_app.bak'):
        remove('pico_app.py')
        os.rename('pico_app.bak', 'pico_app.py')
    remove('pico_app.new')
    remove('ota_trial')


recover()
try:
    with open('pico_app.py', 'r') as file:
        source = file.read()
    exec(compile(source, 'pico_app.py', 'exec'), {'__name__': '__main__'})
except Exception:
    if exists('ota_trial'):
        machine.reset()
    raise
