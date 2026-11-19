"""Single-display mailbox. Pairing keys authorize both controller and device."""
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import hashlib
import secrets
import anvil.server
import anvil.tables
from anvil.tables import app_tables, query as q


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
            'pulse_gap_ms': _pulse_gap(row), 'cooldown_minutes': _cooldown(row)}


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
@anvil.tables.in_transaction
def send_message(key, message):
    row = _device(key)
    if row is None:
        return {'ok': False, 'message': 'Create or enter a pairing key first.'}
    if not isinstance(message, str) or not message.strip():
        return {'ok': False, 'message': 'Enter a message first.'}
    if len(message) > 240 or any(ord(c) < 32 and c != '\n' or ord(c) > 126 for c in message):
        return {'ok': False, 'message': 'Use up to 240 plain English characters (ASCII), including line breaks.'}
    row.update(message=message, revision=row['revision'] + 1)
    return {'ok': True}


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
                                                      'timezone': local_now.tzname(), 'pulse_gap_ms': _pulse_gap(row), 'cooldown_minutes': _cooldown(row), 'text': row['message'], 'revision': row['revision'],
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
