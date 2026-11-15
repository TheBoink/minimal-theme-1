"""Single-display mailbox. Pairing keys authorize both controller and device."""
from datetime import datetime, timezone, timedelta
import hashlib
import secrets
import anvil.server
import anvil.tables
from anvil.tables import app_tables


def _device(key):
    if not isinstance(key, str) or len(key) != 43:
        return None
    return app_tables.displays.get(key_hash=hashlib.sha256(key.encode()).hexdigest())


@anvil.server.callable
def create_display():
    key = secrets.token_urlsafe(32)
    app_tables.displays.add_row(key_hash=hashlib.sha256(key.encode()).hexdigest(),
                            message='', revision=0, displayed_revision=0)
    return key


@anvil.server.callable
def display_status(key):
    row = _device(key)
    if row is None:
        return {'ok': False, 'message': 'Pairing key not recognised.'}
    seen = row['last_seen']
    return {'ok': True, 'online': bool(seen and seen > datetime.now(timezone.utc) - timedelta(seconds=20)),
            'message': row['message'], 'revision': row['revision'],
            'displayed_revision': row['displayed_revision']}


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


@anvil.server.route('/device/message', methods=['GET'])
def device_message(**params):
    row = _device(anvil.server.request.headers.get('authorization', '').removeprefix('Bearer '))
    if row is None:
        return anvil.server.HttpResponse(401, 'Invalid pairing key')
    row['last_seen'] = datetime.now(timezone.utc)
    import json
    return anvil.server.HttpResponse(200, json.dumps({'text': row['message'], 'revision': row['revision']}),
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
