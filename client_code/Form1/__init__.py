from ._anvil_designer import Form1Template
from anvil import *
import anvil.server


class Form1(Form1Template):
    def __init__(self, **properties):
        super().__init__(**properties)
        self.endpoint.text = anvil.server.get_app_origin() + '/device/message'
        self._chart_signature = None
        self.battery_chart.layout = {'paper_bgcolor': '#f5f3ed', 'plot_bgcolor': '#f5f3ed', 'margin': {'l': 50, 'r': 15, 't': 15, 'b': 90}, 'xaxis': {'title': 'Amsterdam time', 'type': 'category', 'tickangle': -45, 'nticks': 8}, 'yaxis': {'title': 'Battery (%)', 'range': [0, 100]}}
        self._settings_key = None
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
        self.status.text = connection


    @handle('button_timer', 'tick')
    def update_button_status(self, **event_args):
        key = self.key_box.text.strip()
        if not key:
            self.drink_schedule_input.text = ''
            self.limit_message_input.text = ''
            self.drink_progress_input.text = ''
            self.drink_checking_input.text = ''
            self.drink_limits_status.text = 'Enter your pairing key to load drink limits.'
            self.button_status.text = 'Waiting for button A… Enter your pairing key first.'
            self.activity_chart.data = []
            self.battery_chart.data = []
            self.battery_status.text = 'Enter your pairing key to see battery status.'
            self.chart_summary.text = 'Enter your pairing key to see 10-minute activity.'
            self._chart_signature = None
            self._settings_key = None
            self.firmware_running_label.text = 'Enter your pairing key to see the running firmware version.'
            self.firmware_published_label.text = 'Enter your pairing key to see the publication time.'
            self.waiting_message_input.text = ''
            self.waiting_message_status.text = 'Enter your pairing key to load the waiting message.'
            self.cooldown_input.text = ''
            self.cooldown_status.text = 'Enter your pairing key to load the waiting period.'
            self.pulse_gap_input.text = ''
            self.pulse_gap_status.text = 'Enter your pairing key to load the saved delay.'
            return
        self.button_timer.interval = 0
        try:
            result = anvil.server.call('display_status', key)
            if not result['ok']:
                self.drink_schedule_input.text = ''
                self.limit_message_input.text = ''
                self.drink_progress_input.text = ''
                self.drink_checking_input.text = ''
                self.drink_limits_status.text = result['message']
                self.firmware_running_label.text = result['message']
                self.firmware_published_label.text = result['message']
                self.button_status.text = result['message']
                if self._settings_key != key:
                    self.pulse_gap_input.text = ''
                    self.cooldown_input.text = ''
                    self.waiting_message_input.text = ''
                    self.waiting_message_status.text = result['message']
                    self.cooldown_status.text = result['message']
                    self.pulse_gap_status.text = result['message']
                    self._settings_key = None
            elif self._settings_key != key:
                self._settings_key = key
                self.drink_schedule_input.text = result['drink_schedule']
                self.limit_message_input.text = result['limit_message']
                self.drink_progress_input.text = result['drink_progress_message']
                self.drink_checking_input.text = result['drink_checking_message']
                self.drink_limits_status.text = 'Saved drink limits loaded.'
                self.waiting_message_input.text = result['waiting_message']
                self.waiting_message_status.text = 'Saved waiting message loaded.'
                self.cooldown_input.text = str(result['cooldown_minutes'])
                self.cooldown_status.text = 'Saved waiting period loaded.'
                self.pulse_gap_input.text = str(result['pulse_gap_ms'])
                self.pulse_gap_status.text = 'Saved delay loaded.'
                self.button_status.text = 'Waiting for button A…' if not result['button_count'] else 'Button A was pressed! · ' + str(int(result['button_count'])) + ' presses · Last: ' + result['button_at'].strftime('%d %b %H:%M:%S')
            elif result['button_count']:
                self.button_status.text = 'Button A was pressed! · ' + str(int(result['button_count'])) + ' presses · Last: ' + result['button_at'].strftime('%d %b %H:%M:%S')
            else:
                self.button_status.text = 'Waiting for button A…'
            if result['ok']:
                self.firmware_running_label.text = 'Running firmware: ' + (result['running_firmware_version'] or 'Unknown (Pico has not reported a version)')
                published = result['firmware_published_at']
                self.firmware_published_label.text = 'Published firmware: ' + (result['published_firmware_version'] or 'None') + ' · ' + published
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
            battery = anvil.server.call('battery_history', key)
            if battery['ok']:
                status = battery['status']
                if status is None:
                    self.battery_status.text = 'No battery readings received in the last 24 hours.'
                else:
                    label = ('Battery approximately ' + str(int(battery['percentage'])) + '%' if status == 'battery' else 'USB power' if status == 'usb' else 'Battery reading unavailable')
                    self.battery_status.text = label + ' · Last received: ' + battery['last_seen']
                self.battery_chart.data = [{'type': 'bar', 'x': battery['times'], 'y': battery['values'], 'marker': {'color': '#28543e'}}]
            else:
                self.battery_chart.data = []
                self.battery_status.text = battery['message']
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




    @handle('pulse_gap_save', 'click')
    def save_pulse_gap(self, **event_args):
        key = (self.key_box.text or '').strip()
        text = (self.pulse_gap_input.text or '').strip()
        if not key:
            self.pulse_gap_status.text = 'Enter your pairing key first.'
            return
        if not text.isdigit() or len(text) > 4 or int(text) > 5000:
            self.pulse_gap_status.text = 'Enter a whole number from 0 to 5000 milliseconds.'
            return
        self.pulse_gap_save.enabled = False
        try:
            result = anvil.server.call('set_pulse_gap', key, int(text))
            if result['ok']:
                self.pulse_gap_status.text = 'Saved. The Pico will use it after its next successful sync.'
            else:
                self.pulse_gap_status.text = result['message']
        finally:
            self.pulse_gap_save.enabled = True


    @handle('cooldown_save', 'click')
    def save_cooldown(self, **event_args):
        key = (self.key_box.text or '').strip()
        text = (self.cooldown_input.text or '').strip()
        if not key:
            self.cooldown_status.text = 'Enter your pairing key first.'
            return
        if not text.isdigit() or len(text) > 4 or int(text) > 1440:
            self.cooldown_status.text = 'Enter a whole number from 0 to 1440 minutes.'
            return
        self.cooldown_save.enabled = False
        try:
            result = anvil.server.call('set_cooldown', key, int(text))
            self.cooldown_status.text = ('Saved. The Pico will use it after its next successful sync.'
                                         if result['ok'] else result['message'])
        finally:
            self.cooldown_save.enabled = True


    @handle('waiting_message_save', 'click')
    def save_waiting_message(self, **event_args):
        key = (self.key_box.text or '').strip()
        if not key:
            self.waiting_message_status.text = 'Enter your pairing key first.'
            return
        self.waiting_message_save.enabled = False
        try:
            result = anvil.server.call('set_waiting_message', key, self.waiting_message_input.text or '')
            self.waiting_message_status.text = ('Saved. The Pico will use it after its next successful sync.'
                                                if result['ok'] else result['message'])
        finally:
            self.waiting_message_save.enabled = True


    @handle('firmware_publish', 'click')
    def publish_firmware(self, **event_args):
        key = (self.key_box.text or '').strip()
        signing_key = self.firmware_key.text or ''
        if not key:
            self.firmware_status.text = 'Enter your pairing key first.'
            return
        if self.firmware_file.file is None:
            self.firmware_status.text = 'Choose a firmware file first.'
            return
        self.firmware_publish.enabled = False
        try:
            result = anvil.server.call('publish_firmware', key, self.firmware_file.file, signing_key)
            if result['ok']:
                self.firmware_status.text = 'Firmware published. The Pico will check for it while connected.'
                self.firmware_published_label.text = 'Published firmware: ' + result['digest'] + ' · ' + result['firmware_published_at']
                self.firmware_key.text = ''
            else:
                self.firmware_status.text = result['message']
        finally:
            self.firmware_publish.enabled = True


    @handle('drink_limits_save', 'click')
    def save_drink_limits(self, **event_args):
        key = self.key_box.text.strip()
        self.drink_limits_save.enabled = False
        try:
            result = anvil.server.call('set_drink_limits', key, self.drink_schedule_input.text or '',
                                       self.limit_message_input.text or '', self.drink_progress_input.text or '',
                                       self.drink_checking_input.text or '')
            self.drink_limits_status.text = 'Saved. Applies to the next checked press.' if result['ok'] else result['message']
        finally:
            self.drink_limits_save.enabled = True
