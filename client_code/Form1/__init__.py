from ._anvil_designer import Form1Template
from anvil import *
import anvil.server


class Form1(Form1Template):
    def __init__(self, **properties):
        super().__init__(**properties)
        self.endpoint.text = anvil.server.get_app_origin() + '/device/message'
        self.preview.text = 'Hello, little world.'
        self._chart_signature = None
        self.activity_chart.layout = {
            'paper_bgcolor': '#f5f3ed', 'plot_bgcolor': '#f5f3ed',
            'font': {'color': '#263a32'}, 'margin': {'l': 50, 'r': 15, 't': 15, 'b': 90},
            'xaxis': {'title': '10-minute interval start (UTC)', 'type': 'category', 'tickangle': -45, 'nticks': 8},
            'yaxis': {'title': 'Button presses', 'rangemode': 'tozero', 'dtick': 1},
            'showlegend': False}


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


    @handle('button_timer', 'tick')
    def update_button_status(self, **event_args):
        key = self.key_box.text.strip()
        if not key:
            self.button_status.text = 'Waiting for button A… Enter your pairing key first.'
            self.activity_chart.data = []
            self.chart_summary.text = 'Enter your pairing key to see 10-minute activity.'
            self._chart_signature = None
            return
        self.button_timer.interval = 0
        try:
            result = anvil.server.call('display_status', key)
            if not result['ok']:
                self.button_status.text = result['message']
            elif result['button_count']:
                self.button_status.text = 'Button A was pressed! · ' + str(int(result['button_count'])) + ' presses · Last: ' + result['button_at'].strftime('%d %b %H:%M:%S')
            else:
                self.button_status.text = 'Waiting for button A…'
            signature = (key, result.get('button_count'))
            if signature != self._chart_signature:
                self.refresh_activity_chart()
                self._chart_signature = signature
        finally:
            self.button_timer.interval = 2


    @handle('chart_timer', 'tick')
    def refresh_activity_chart(self, **event_args):
        key = self.key_box.text.strip()
        if not key:
            return
        self.chart_timer.interval = 0
        try:
            result = anvil.server.call('ten_minute_button_presses', key)
            if not result['ok']:
                self.activity_chart.data = []
                self.chart_summary.text = result['message']
                return
            self.activity_chart.data = [{
                'type': 'bar', 'x': result['intervals'], 'y': result['counts'],
                'marker': {'color': '#28543e'},
                'hovertemplate': '10 minutes from %{x} UTC<br>%{y} presses<extra></extra>'}]
            self.chart_summary.text = str(result['total']) + ' presses in the displayed 24 hours'
        finally:
            self.chart_timer.interval = 30


