from ._anvil_designer import Form1Template
from anvil import *
import anvil.server


class Form1(Form1Template):
    def __init__(self, **properties):
        super().__init__(**properties)
        self.endpoint.text = anvil.server.get_app_origin() + '/device/message'
        self.preview.text = 'Hello, little world.'

    @handle('pair_button', 'click')
    def pair(self, **event_args):
        self.pair_button.enabled = False
        try:
            self.key_box.text = anvil.server.call('create_display')
            self.status.text = 'Pairing key created. Save it, then add it to your Pico firmware.'
        finally:
            self.pair_button.enabled = True

    @handle('message_box', 'change')
    def preview_message(self, **event_args):
        text = self.message_box.text or ''
        self.preview.text = text or 'Your message here'
        self.counter.text = str(len(text)) + ' / 240 characters'

    @handle('send_button', 'click')
    def send(self, **event_args):
        self.send_button.enabled = False
        try:
            result = anvil.server.call('send_message', self.key_box.text.strip(), self.message_box.text)
            self.status.text = 'Queued. Waiting for the Pico to confirm display.' if result['ok'] else result['message']
            if result['ok']:
                self.refresh_status()
        finally:
            self.send_button.enabled = True

    @handle('refresh_button', 'click')
    def refresh_status(self, **event_args):
        if not self.key_box.text.strip():
            self.status.text = 'Create or enter your pairing key first.'
            return
        result = anvil.server.call('display_status', self.key_box.text.strip())
        if not result['ok']:
            self.status.text = result['message']
            return
        connection = 'Pico online' if result['online'] else 'Pico offline / not connected'
        delivery = 'No messages sent yet'
        if result['revision']:
            delivery = 'Display confirmed' if result['displayed_revision'] == result['revision'] else 'Latest message queued'
        self.status.text = connection + ' · ' + delivery
