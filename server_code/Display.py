"""Single-display mailbox. Pairing keys authorize both controller and device."""
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import ast
import base64
import hashlib
import hmac
import secrets
import anvil
import anvil.server
import anvil.tables
from anvil.tables import app_tables, query as q


DEFAULT_WAITING_MESSAGE = 'Even geduld voor de volgende borrel (nog {minutes} minuten).'


def _wrap_waiting_message(text):
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


def _waiting_message(row):
    value = row['waiting_message']
    return DEFAULT_WAITING_MESSAGE if value is None else value


def _device(key):
    if not isinstance(key, str) or len(key) != 43:
        return None
    return app_tables.displays.get(key_hash=hashlib.sha256(key.encode()).hexdigest())


@anvil.server.callable
def create_display():
    key = secrets.token_urlsafe(32)
    app_tables.displays.add_row(key_hash=hashlib.sha256(key.encode()).hexdigest(),
                            message='', revision=0, displayed_revision=0, pulse_gap_ms=500, cooldown_minutes=0)
    return key


@anvil.server.callable
def display_status(key):
    row = _device(key)
    if row is None:
        return {'ok': False, 'message': 'Pairing key not recognised.'}
    seen = row['last_seen']
    return {'ok': True, 'online': bool(seen and seen > datetime.now(timezone.utc) - timedelta(seconds=20)),
            'message': row['message'], 'revision': row['revision'],
            'displayed_revision': row['displayed_revision'],
            'button_count': row['button_count'] or 0, 'button_at': row['button_at'],
            'pulse_gap_ms': _pulse_gap(row), 'cooldown_minutes': _cooldown(row),
            'waiting_message': _waiting_message(row),
            'firmware_published_at': _firmware_publication_text(row)}


def _firmware_publication_text(row):
    published = row['firmware_published_at']
    if published is not None:
        return published.astimezone(AMSTERDAM).strftime('%d %b %Y %H:%M:%S %Z')
    if row['firmware_hash']:
        return 'Publication time unavailable for existing firmware'
    return 'No firmware published yet'


def _pulse_gap(row):
    value = row['pulse_gap_ms']
    return 500 if value is None else int(value)


@anvil.server.callable
def set_pulse_gap(key, value):
    row = _device(key)
    if row is None:
        return {'ok': False, 'message': 'Create or enter a valid pairing key first.'}
    if type(value) is not int or not 0 <= value <= 5000:
        return {'ok': False, 'message': 'Enter a whole number from 0 to 5000 milliseconds.'}
    row['pulse_gap_ms'] = value
    return {'ok': True}


def _cooldown(row):
    value = row['cooldown_minutes']
    return 0 if value is None else int(value)


@anvil.server.callable
def set_cooldown(key, value):
    row = _device(key)
    if row is None:
        return {'ok': False, 'message': 'Create or enter a valid pairing key first.'}
    if type(value) is not int or not 0 <= value <= 1440:
        return {'ok': False, 'message': 'Enter a whole number from 0 to 1440 minutes.'}
    row['cooldown_minutes'] = value
    return {'ok': True}


@anvil.server.callable
def set_waiting_message(key, value):
    row = _device(key)
    if row is None:
        return {'ok': False, 'message': 'Create or enter a valid pairing key first.'}
    if not isinstance(value, str) or value.count('{minutes}') != 1:
        return {'ok': False, 'message': 'Include {minutes} exactly once in the message.'}
    if len(value) > 160 or any(not (32 <= ord(c) <= 126 or c == '\n') for c in value):
        return {'ok': False, 'message': 'Use up to 160 basic English characters and line breaks.'}
    if any(len(_wrap_waiting_message(value.replace('{minutes}', n))) > 5 for n in ('1', '10', '100', '1440')):
        return {'ok': False, 'message': 'Message is too long for the LCD. Shorten it to fit five lines.'}
    row['waiting_message'] = value
    return {'ok': True}


@anvil.server.callable
def publish_firmware(key, media, signing_key):
    display = _device(key)
    if display is None:
        return {'ok': False, 'message': 'Enter a valid pairing key first.'}
    if not isinstance(signing_key, str) or not 32 <= len(signing_key) <= 128 or not signing_key.isascii():
        return {'ok': False, 'message': 'Enter the same 32–128 character ASCII update signing key stored on the Pico.'}
    if not isinstance(media, anvil.Media) or media.length == 0 or media.length > 65536:
        return {'ok': False, 'message': 'Choose a Python firmware file up to 64 KB.'}
    data = media.get_bytes()
    try:
        source = data.decode('utf-8')
        tree = ast.parse(source)
    except (UnicodeDecodeError, SyntaxError):
        return {'ok': False, 'message': 'The firmware must be valid UTF-8 Python code.'}
    if not any(isinstance(node, ast.FunctionDef) and node.name == 'main' for node in tree.body):
        return {'ok': False, 'message': 'The firmware must contain a top-level main function.'}
    signature = hmac.new(signing_key.encode('utf-8'), data, hashlib.sha256).hexdigest()
    digest = hashlib.sha256(data).hexdigest()
    published_at = datetime.now(timezone.utc)
    display.update(firmware=anvil.BlobMedia('text/x-python', data, name='pico_app.py'),
                   firmware_signature=signature, firmware_hash=digest,
                   firmware_published_at=published_at)
    return {'ok': True, 'digest': digest[:12],
            'firmware_published_at': published_at.astimezone(AMSTERDAM).strftime('%d %b %Y %H:%M:%S %Z')}


@anvil.server.route('/device/firmware', methods=['GET'])
def device_firmware(**params):
    display = _device(anvil.server.request.headers.get('authorization', '').removeprefix('Bearer '))
    if display is None:
        return anvil.server.HttpResponse(401, 'Invalid pairing key')
    media = display['firmware']
    if media is None:
        return anvil.server.HttpResponse(204, '')
    import json
    body = json.dumps({'data': base64.b64encode(media.get_bytes()).decode('ascii'),
                       'signature': display['firmware_signature']})
    return anvil.server.HttpResponse(200, body, headers={'Content-Type': 'application/json', 'Cache-Control': 'no-store'})


AMSTERDAM = ZoneInfo("Europe/Amsterdam")


def _daily_activity(display, now=None):
    now = now if now is not None else datetime.now(timezone.utc)
    local_now = now.astimezone(AMSTERDAM)
    start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    oldest = start - timedelta(days=2)
    counts = [[0] * 12 for _ in range(3)]
    for press in app_tables.button_presses.search(
            q.fetch_only('received_at'), display=display, button='A',
            received_at=q.all_of(q.greater_than_or_equal_to(oldest),
                                q.less_than(start + timedelta(days=1)))):
        local = press['received_at'].astimezone(AMSTERDAM)
        day_index = (local.date() - oldest.date()).days
        counts[day_index][local.hour // 2] += 1
    months = ('JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
              'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC')
    days = []
    for index in (2, 1, 0):
        day = oldest + timedelta(days=index)
        days.append({'date': '%02d %s' % (day.day, months[day.month - 1]),
                     'slots': [count > 0 for count in counts[index]], 'counts': counts[index]})
    # Keep today's fields for receivers running the previous firmware.
    return dict(days[0], days=days)


@anvil.server.route('/device/message', methods=['GET'])
def device_message(**params):
    row = _device(anvil.server.request.headers.get('authorization', '').removeprefix('Bearer '))
    if row is None:
        return anvil.server.HttpResponse(401, 'Invalid pairing key')
    row['last_seen'] = datetime.now(timezone.utc)
    import json
    local_now = datetime.now(AMSTERDAM)
    offset = local_now.utcoffset()
    assert offset is not None
    return anvil.server.HttpResponse(200, json.dumps({'utc_offset': int(offset.total_seconds()),
                                                      'timezone': local_now.tzname(), 'pulse_gap_ms': _pulse_gap(row), 'cooldown_minutes': _cooldown(row), 'waiting_message': _waiting_message(row), 'text': row['message'], 'revision': row['revision'],
                                                      'activity': _daily_activity(row)}),
                                    headers={'Content-Type': 'application/json', 'Cache-Control': 'no-store'})


@anvil.server.route('/device/ack', methods=['POST'])
@anvil.tables.in_transaction
def device_ack(**params):
    row = _device(anvil.server.request.headers.get('authorization', '').removeprefix('Bearer '))
    if row is None:
        return anvil.server.HttpResponse(401, 'Invalid pairing key')
    data = anvil.server.request.body_json
    if not isinstance(data, dict) or type(data.get('revision')) is not int:
        return anvil.server.HttpResponse(400, 'Integer revision required')
    revision = data['revision']
    if revision < 0 or revision > row['revision']:
        return anvil.server.HttpResponse(400, 'Invalid revision')
    row.update(displayed_revision=max(row['displayed_revision'], revision), last_seen=datetime.now(timezone.utc))
    return anvil.server.HttpResponse(200, 'OK')


@anvil.server.route('/device/button', methods=['POST'])
@anvil.tables.in_transaction
def device_button(**params):
    row = _device(anvil.server.request.headers.get('authorization', '').removeprefix('Bearer '))
    if row is None:
        return anvil.server.HttpResponse(401, 'Invalid pairing key')
    data = anvil.server.request.body_json
    if (not isinstance(data, dict) or data.get('button') != 'A'
            or type(data.get('count')) is not int or not 1 <= data['count'] <= 1000
            or not isinstance(data.get('event_id'), str) or not 1 <= len(data['event_id']) <= 80):
        return anvil.server.HttpResponse(400, 'Invalid button event')
    # Older firmware omits the outcome; it cannot be reconstructed reliably.
    outcome = data.get('drink_triggered')
    if ('drink_triggered' in data and (type(outcome) is not bool or data['count'] != 1)):
        return anvil.server.HttpResponse(400, 'Invalid drink outcome')
    # A permanent event lookup also protects against old retries arriving later.
    if app_tables.button_presses.get(display=row, event_id=data['event_id'], press_in_event=1) is None:
        received_at = datetime.now(timezone.utc)
        for number in range(1, data['count'] + 1):
            app_tables.button_presses.add_row(
                display=row, button='A', received_at=received_at,
                event_id=data['event_id'], press_in_event=number, drink_triggered=outcome)
        row.update(button_count=(row['button_count'] or 0) + data['count'],
                   button_at=received_at, button_event_id=data['event_id'],
                   last_seen=received_at)
    return anvil.server.HttpResponse(200, 'OK')


@anvil.server.callable
def ten_minute_button_presses(key):
    display = _device(key)
    if display is None:
        return {'ok': False, 'message': 'Pairing key not recognised.'}
    now = datetime.now(timezone.utc)
    current_interval = now.replace(minute=(now.minute // 10) * 10, second=0, microsecond=0)
    start = current_interval - timedelta(minutes=1430)
    end = current_interval + timedelta(minutes=10)
    counts = [0] * 144
    for press in app_tables.button_presses.search(
            q.fetch_only('received_at'), display=display, button='A',
            received_at=q.all_of(q.greater_than_or_equal_to(start), q.less_than(end))):
        index = int((press['received_at'] - start).total_seconds() // 600)
        counts[index] += 1
    return {'ok': True, 'intervals': [(start + timedelta(minutes=10 * i)).strftime('%d %b %H:%M') for i in range(144)],
            'counts': counts, 'total': sum(counts)}
