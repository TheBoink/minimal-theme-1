"""Single-display mailbox. Pairing keys authorize both controller and device."""
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import ast
import re
import base64
import hashlib
import hmac
import secrets
import anvil
import anvil.server
import anvil.tables
from anvil.tables import app_tables, query as q


DEFAULT_CHECKING_MESSAGE = 'One moment please...'
DEFAULT_PROGRESS_MESSAGE = 'This is drink\n{drink} out of a\nmaximum of {max}\n{period}'
DEFAULT_LIMIT_MESSAGE = 'Drink limit reached until {end}.'

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
            'drink_schedule': row['drink_schedule'] or '',
            'limit_message': row['limit_message'] or DEFAULT_LIMIT_MESSAGE,
            'drink_checking_message': row['drink_checking_message'] or DEFAULT_CHECKING_MESSAGE,
            'drink_progress_message': row['drink_progress_message'] or DEFAULT_PROGRESS_MESSAGE,
            'firmware_published_at': _firmware_publication_text(row),
            'published_firmware_version': row['firmware_hash'][:12] if row['firmware_hash'] else None,
            'running_firmware_version': row['running_firmware_version']}


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


def _periods(schedule):
    periods = []
    for line in schedule.splitlines():
        match = re.fullmatch(r'([0-9]{2}):([0-9]{2})-([0-9]{2}):([0-9]{2})\s+([0-9]{1,3})', line.strip())
        if not match:
            raise ValueError('Use HH:MM-HH:MM maximum on each line.')
        sh, sm, eh, em, maximum = map(int, match.groups())
        start, end = sh * 60 + sm, eh * 60 + em
        if sh >= 24 or sm >= 60 or em >= 60 or end > 1440:
            raise ValueError('Enter valid times. Use 24:00 only as an end time.')
        end = end or 1440
        if end <= start or any(start < b and a < end for a, b, _ in periods):
            raise ValueError('Periods must not overlap. Split overnight periods at midnight.')
        periods.append((start, end, maximum))
    return periods


@anvil.server.callable
def set_drink_limits(key, schedule, message, progress=None, checking=None):
    row = _device(key)
    if row is None:
        return {'ok': False, 'message': 'Enter a valid pairing key first.'}
    if not isinstance(schedule, str) or len(schedule) > 2000:
        return {'ok': False, 'message': 'Schedule is too long.'}
    schedule = schedule.strip()
    try:
        _periods(schedule)
    except ValueError as error:
        return {'ok': False, 'message': str(error)}
    if (not isinstance(message, str) or len(message) > 160 or message.count('{end}') > 1 or ('{end}' not in message and '{period}' not in message)
            or any(not (32 <= ord(c) <= 126 or c == '\n') for c in message)
            or any(len(_wrap_waiting_message(message.replace('{end}', '23:59').replace('{max}', maximum).replace('{period}', '15:00-21:00'))) > 5
                   for maximum in ('0', '99', '999'))):
        return {'ok': False, 'message': 'Include {end} or {period} and use basic characters that fit five LCD lines.'}
    if progress is None:
        progress = row['drink_progress_message'] or DEFAULT_PROGRESS_MESSAGE
    if (not isinstance(progress, str) or len(progress) > 160
            or any(progress.count(token) != 1 for token in ('{drink}', '{max}', '{period}'))
            or any(not (32 <= ord(c) <= 126 or c == '\n') for c in progress)):
        return {'ok': False, 'message': 'Use basic characters and include {drink}, {max}, and {period} once each.'}
    for drink in ('1', '10', '100'):
        for maximum in ('9', '99', '999'):
            rendered = progress.replace('{drink}', drink).replace('{max}', maximum).replace('{period}', '15:00-21:00')
            if len(_wrap_waiting_message(rendered)) > 5:
                return {'ok': False, 'message': 'Shorten the drink delivery message to fit five LCD lines.'}
    if checking is None:
        checking = row['drink_checking_message'] or DEFAULT_CHECKING_MESSAGE
    if (not isinstance(checking, str) or not checking.strip() or len(checking) > 160
            or any(not (32 <= ord(c) <= 126 or c == '\n') for c in checking)
            or len(_wrap_waiting_message(checking)) > 5):
        return {'ok': False, 'message': 'Enter a checking message using basic characters that fits five LCD lines.'}
    row.update(drink_schedule=schedule, limit_message=message,
               drink_progress_message=progress, drink_checking_message=checking)
    return {'ok': True}


def _drink_decision(display, now):
    local = now.astimezone(AMSTERDAM)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    def boundary(minutes):
        wall = midnight + timedelta(minutes=minutes)
        while True:
            utc = wall.astimezone(timezone.utc)
            if utc.astimezone(AMSTERDAM).replace(tzinfo=None) == wall.replace(tzinfo=None):
                return utc
            # A spring-forward boundary inside the skipped hour starts at 03:00.
            wall += timedelta(minutes=1)

    for start, end, maximum in _periods(display['drink_schedule'] or ''):
        beginning, ending = boundary(start), boundary(end)
        if not beginning <= now < ending:
            continue
        used = 0
        if maximum:
            for press in app_tables.button_presses.search(
                    display=display, received_at=q.all_of(
                        q.greater_than_or_equal_to(beginning), q.less_than(ending))):
                if press['drink_reserved'] or press['drink_triggered']:
                    used += 1
        end_text = ending.astimezone(AMSTERDAM).strftime('%H:%M')
        start_text = beginning.astimezone(AMSTERDAM).strftime('%H:%M')
        period_text = f'{start_text}-{end_text}'
        if used >= maximum:
            template = display['limit_message'] or DEFAULT_LIMIT_MESSAGE
            return (template.replace('{end}', end_text).replace('{max}', str(maximum))
                    .replace('{period}', period_text)), ''
        template = display['drink_progress_message'] or DEFAULT_PROGRESS_MESSAGE
        return '', (template.replace('{drink}', str(used + 1)).replace('{max}', str(maximum))
                    .replace('{period}', period_text))
    return '', ''


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
    version = anvil.server.request.headers.get('x-firmware-version')
    if version is not None:
        if len(version) != 12 or any(c not in '0123456789abcdef' for c in version):
            return anvil.server.HttpResponse(400, 'Invalid firmware version')
        row['running_firmware_version'] = version
    row['last_seen'] = datetime.now(timezone.utc)
    import json
    local_now = datetime.now(AMSTERDAM)
    offset = local_now.utcoffset()
    assert offset is not None
    return anvil.server.HttpResponse(200, json.dumps({'utc_offset': int(offset.total_seconds()),
                                                      'timezone': local_now.tzname(), 'pulse_gap_ms': _pulse_gap(row), 'cooldown_minutes': _cooldown(row), 'waiting_message': _waiting_message(row), 'text': row['message'], 'revision': row['revision'],
                                                      'drink_checking_message': row['drink_checking_message'] or DEFAULT_CHECKING_MESSAGE,
                                                      'activity': _daily_activity(row)}),
                                    headers={'Content-Type': 'application/json', 'Cache-Control': 'no-store'})


@anvil.server.route('/device/battery', methods=['POST'])
@anvil.tables.in_transaction
def device_battery(**params):
    display = _device(anvil.server.request.headers.get('authorization', '').removeprefix('Bearer '))
    if display is None:
        return anvil.server.HttpResponse(401, 'Invalid pairing key')
    data = anvil.server.request.body_json
    if not isinstance(data, dict):
        return anvil.server.HttpResponse(400, 'Invalid battery reading')
    event_id = data.get('event_id')
    status = data.get('status')
    percentage = data.get('percentage')
    if (not isinstance(event_id, str) or not 1 <= len(event_id) <= 80 or
            status not in ('battery', 'usb', 'unavailable') or
            (status == 'battery' and (type(percentage) is not int or not 0 <= percentage <= 100)) or
            (status != 'battery' and percentage is not None)):
        return anvil.server.HttpResponse(400, 'Invalid battery reading')
    if app_tables.battery_readings.get(display=display, event_id=event_id) is None:
        app_tables.battery_readings.add_row(display=display, event_id=event_id,
            received_at=datetime.now(timezone.utc), status=status, percentage=percentage)
    return anvil.server.HttpResponse(200, 'OK')


@anvil.server.callable
def battery_history(key):
    display = _device(key)
    if display is None:
        return {'ok': False, 'message': 'Pairing key not recognised.'}
    readings = list(app_tables.battery_readings.search(
        display=display, received_at=q.greater_than_or_equal_to(datetime.now(timezone.utc) - timedelta(hours=24))))
    readings.sort(key=lambda row: row['received_at'])
    latest = readings[-1] if readings else None
    return {'ok': True,
            'status': latest['status'] if latest else None,
            'percentage': latest['percentage'] if latest else None,
            'last_seen': latest['received_at'].astimezone(AMSTERDAM).strftime('%d %b %H:%M:%S %Z') if latest else None,
            'times': [row['received_at'].astimezone(AMSTERDAM).strftime('%d %b %H:%M:%S %Z') for row in readings if row['status'] == 'battery'],
            'values': [row['percentage'] for row in readings if row['status'] == 'battery']}


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
    if data.get('authorize') is True:
        gap = data.get('pulse_gap_ms')
        if data['count'] != 1 or type(gap) is not int or not 0 <= gap <= 5000:
            return anvil.server.HttpResponse(400, 'Invalid authorization request')
        import json
        existing = app_tables.button_presses.get(display=row, event_id=data['event_id'], press_in_event=1)
        progress_message = ''
        if existing is not None:
            decision = 'Already checked. Press again.'
        else:
            now = datetime.now(timezone.utc)
            decision, progress_message = _drink_decision(row, now)
            cooldown = _cooldown(row)
            if not decision and cooldown:
                cutoff = now - timedelta(minutes=cooldown)
                prior = app_tables.button_presses.search(
                    display=row, received_at=q.greater_than(cutoff))
                latest = max((press['received_at'] for press in prior
                              if press['drink_reserved'] or press['drink_triggered']), default=None)
                if latest is not None:
                    remaining = latest + timedelta(minutes=cooldown) - now
                    minutes = (remaining + timedelta(minutes=1, microseconds=-1)) // timedelta(minutes=1)
                    decision = _waiting_message(row).replace('{minutes}', str(minutes))
            app_tables.button_presses.add_row(display=row, button='A', received_at=now,
                event_id=data['event_id'], press_in_event=1, pulse_gap_ms=gap,
                drink_reserved=not decision, drink_triggered=False if decision else None)
            row.update(button_count=(row['button_count'] or 0) + 1,
                       button_at=now, button_event_id=data['event_id'], last_seen=now)
        return anvil.server.HttpResponse(200, json.dumps({'allowed': not decision, 'message': decision or progress_message}),
                                         headers={'Content-Type': 'application/json'})
    # Older firmware omits the outcome; it cannot be reconstructed reliably.
    outcome = data.get('drink_triggered')
    if ('drink_triggered' in data and (type(outcome) is not bool or data['count'] != 1)):
        return anvil.server.HttpResponse(400, 'Invalid drink outcome')
    pulse_gap = data.get('pulse_gap_ms')
    if 'pulse_gap_ms' in data and (type(pulse_gap) is not int or not 0 <= pulse_gap <= 5000 or data['count'] != 1):
        return anvil.server.HttpResponse(400, 'Invalid pulse delay')
    # A permanent event lookup also protects against old retries arriving later.
    existing = app_tables.button_presses.get(display=row, event_id=data['event_id'], press_in_event=1)
    if existing is not None and existing['drink_reserved'] and outcome is True:
        existing['drink_triggered'] = True
    if existing is None:
        received_at = datetime.now(timezone.utc)
        for number in range(1, data['count'] + 1):
            app_tables.button_presses.add_row(
                display=row, button='A', received_at=received_at,
                event_id=data['event_id'], press_in_event=number, drink_triggered=outcome,
                pulse_gap_ms=pulse_gap)
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


@anvil.server.callable
def daily_drink_activity(key):
    display = _device(key)
    if display is None:
        return {'ok': False, 'message': 'Pairing key not recognised.'}
    today = datetime.now(AMSTERDAM).replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=13)
    end = today + timedelta(days=1)
    presses, drinks = [0] * 14, [0] * 14
    duration_ms = [0.0] * 14
    missing_delay = 0
    for press in app_tables.button_presses.search(
            q.fetch_only('received_at', 'drink_triggered', 'pulse_gap_ms'), display=display, button='A',
            received_at=q.all_of(q.greater_than_or_equal_to(start.astimezone(timezone.utc)),
                                 q.less_than(end.astimezone(timezone.utc)))):
        day = (press['received_at'].astimezone(AMSTERDAM).date() - start.date()).days
        presses[day] += 1
        if press['drink_triggered'] is True:
            drinks[day] += 1
            delay = press['pulse_gap_ms']
            if delay is None:
                missing_delay += 1
            else:
                duration_ms[day] += delay
    days = [(start + timedelta(days=i)).strftime('%d %b') for i in range(14)]
    return {'ok': True, 'days': days, 'presses': presses, 'drinks': drinks,
            'millilitres': [round(value / 75, 2) for value in duration_ms],
            'missing_delay': missing_delay}


@anvil.server.route('/device/door', methods=['POST'])
@anvil.tables.in_transaction
def device_door(**params):
    display = _device(anvil.server.request.headers.get('authorization', '').removeprefix('Bearer '))
    if display is None:
        return anvil.server.HttpResponse(401, 'Invalid pairing key')
    data = anvil.server.request.body_json
    if not isinstance(data, dict):
        return anvil.server.HttpResponse(400, 'Invalid door event')
    event_id, timestamp = data.get('event_id'), data.get('opened_at')
    now = datetime.now(timezone.utc)
    if (not isinstance(event_id, str) or not 1 <= len(event_id) <= 80 or
            (timestamp is not None and (type(timestamp) is not int or not 0 <= timestamp <= now.timestamp() + 300))):
        return anvil.server.HttpResponse(400, 'Invalid door event')
    if app_tables.door_openings.get(display=display, event_id=event_id) is None:
        opened = datetime.fromtimestamp(timestamp, timezone.utc) if timestamp is not None else None
        app_tables.door_openings.add_row(display=display, event_id=event_id, opened_at=opened, received_at=now)
    return anvil.server.HttpResponse(200, 'OK')


@anvil.server.callable
def door_history(key):
    display = _device(key)
    if display is None:
        return {'ok': False, 'message': 'Pairing key not recognised.'}
    rows = app_tables.door_openings.search(anvil.tables.order_by('received_at', ascending=False), display=display)
    openings = []
    for row in rows:
        opened = row['opened_at']
        stamp = (opened or row['received_at']).astimezone(AMSTERDAM).strftime('%d %b %H:%M:%S %Z')
        openings.append(stamp if opened is not None else stamp + ' (received)')
        if len(openings) == 5:
            break
    return {'ok': True, 'openings': openings}


@anvil.server.route('/device/tilt', methods=['POST'])
@anvil.tables.in_transaction
def device_tilt(**params):
    display = _device(anvil.server.request.headers.get('authorization', '').removeprefix('Bearer '))
    if display is None:
        return anvil.server.HttpResponse(401, 'Invalid pairing key')
    data = anvil.server.request.body_json
    if not isinstance(data, dict):
        return anvil.server.HttpResponse(400, 'Invalid tilt event')
    event_id, timestamp = data.get('event_id'), data.get('tilted_at')
    now = datetime.now(timezone.utc)
    if (not isinstance(event_id, str) or not 1 <= len(event_id) <= 80 or
            (timestamp is not None and (type(timestamp) is not int or not 0 <= timestamp <= now.timestamp() + 300))):
        return anvil.server.HttpResponse(400, 'Invalid tilt event')
    if app_tables.box_tilts.get(display=display, event_id=event_id) is None:
        tilted = datetime.fromtimestamp(timestamp, timezone.utc) if timestamp is not None else None
        app_tables.box_tilts.add_row(display=display, event_id=event_id, tilted_at=tilted, received_at=now)
    return anvil.server.HttpResponse(200, 'OK')


@anvil.server.callable
def tilt_history(key):
    display = _device(key)
    if display is None:
        return {'ok': False, 'message': 'Pairing key not recognised.'}
    rows = app_tables.box_tilts.search(anvil.tables.order_by('received_at', ascending=False), display=display)
    tilts = []
    for row in rows:
        tilted = row['tilted_at']
        stamp = (tilted or row['received_at']).astimezone(AMSTERDAM).strftime('%d %b %H:%M:%S %Z')
        tilts.append(stamp if tilted is not None else stamp + ' (received)')
        if len(tilts) == 5:
            break
    return {'ok': True, 'tilts': tilts}


@anvil.server.callable
def daily_box_activity(key):
    display = _device(key)
    if display is None:
        return {'ok': False, 'message': 'Pairing key not recognised.'}
    today = datetime.now(AMSTERDAM).replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=13)
    days = [(start + timedelta(days=i)).strftime('%d %b') for i in range(14)]
    result = {'ok': True, 'days': days}
    for table, timestamp, name in ((app_tables.door_openings, 'opened_at', 'openings'),
                                   (app_tables.box_tilts, 'tilted_at', 'tilts')):
        counts = [0] * 14
        for row in table.search(q.fetch_only(timestamp, 'received_at'), display=display,
                                received_at=q.greater_than_or_equal_to(start.astimezone(timezone.utc) - timedelta(minutes=5))):
            event_time = row[timestamp] or row['received_at']
            index = (event_time.astimezone(AMSTERDAM).date() - start.date()).days
            if 0 <= index < 14:
                counts[index] += 1
        result[name] = counts
    return result
