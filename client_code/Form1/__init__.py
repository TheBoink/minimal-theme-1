from ._anvil_designer import Form1Template
from anvil import *
import anvil.server
import anvil.users


class Form1(Form1Template):
    def __init__(self, **properties):
        super().__init__(**properties)
        access = anvil.server.call('dashboard_access')
        self._assigned_display = access['assigned']
        self._needs_device = ('Enter a pairing key or create a DrinkBuddy.' if access['admin']
                              else 'Ask your administrator to assign a DrinkBuddy.')
        self.users_button.visible = access['admin']
        self.pair_button.visible = access['admin']
        self.key_box.visible = access['admin']
        self.status.text = 'Connecting to your assigned DrinkBuddy…' if self._assigned_display else self._needs_device
        self.endpoint.text = anvil.server.get_app_origin() + '/device/message'
        self._battery_chart_key = ''
        self.battery_chart.layout = {'paper_bgcolor': '#f5f3ed', 'plot_bgcolor': '#f5f3ed', 'margin': {'l': 50, 'r': 15, 't': 15, 'b': 90}, 'xaxis': {'title': 'Amsterdam time', 'type': 'category', 'tickangle': -45, 'nticks': 8}, 'yaxis': {'title': 'Battery (%)', 'range': [0, 100]}}
        self.box_chart.layout = {
            'barmode': 'group',
            'margin': {'l': 48, 'r': 20, 't': 25, 'b': 90},
            'xaxis': {'type': 'category', 'tickangle': -45, 'nticks': 8},
            'yaxis': {'title': 'Events', 'rangemode': 'tozero', 'dtick': 1},
            'legend': {'orientation': 'h', 'y': 1.15},
        }
        self.drink_chart.layout = {
            'barmode': 'group',
            'margin': {'l': 54, 'r': 54, 't': 12, 'b': 95},
            'xaxis': {'type': 'category', 'tickangle': -45, 'nticks': 8},
            'yaxis': {'title': 'Count', 'rangemode': 'tozero'},
            'yaxis2': {'title': 'Estimated mL', 'overlaying': 'y', 'side': 'right', 'rangemode': 'tozero'},
            'legend': {'orientation': 'h', 'y': 1.12},
        }
        self._settings_key = ''


    def _dashboard_key(self):
        key = (self.key_box.text or '').strip()
        if not key and self._assigned_display:
            return None
        return key

    @handle('users_button', 'click')
    def manage_users(self, **event_args):
        open_form('UserAdmin')

    @handle('sign_out_button', 'click')
    def sign_out(self, **event_args):
        anvil.users.logout(invalidate_client_objects=True)
        open_form('Login')

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
        if self._dashboard_key() == '':
            self.status.text = self._needs_device
            return
        result = anvil.server.call('display_status', self._dashboard_key())
        if not result['ok']:
            self.status.text = result['message']
            return
        connection = 'Pico online' if result['online'] else 'Pico offline / not connected'
        self.status.text = connection


    @handle('button_timer', 'tick')
    def update_button_status(self, **event_args):
        key = self._dashboard_key()
        if key == '':
            self.drink_schedule_input.text = ''
            self.limit_message_input.text = ''
            self.drink_progress_input.text = ''
            self.drink_checking_input.text = ''
            self.drink_limits_status.text = self._needs_device
            self.button_status.text = self._needs_device
            self.box_chart.data = []
            self.box_chart_status.text = self._needs_device
            self.drink_chart.data = []
            self.drink_chart_status.text = self._needs_device
            self.battery_chart.data = []
            self.battery_status.text = self._needs_device
            self._battery_chart_key = ''
            self._settings_key = ''
            self.firmware_running_label.text = self._needs_device
            self.firmware_published_label.text = self._needs_device
            self.waiting_message_input.text = ''
            self.waiting_message_status.text = self._needs_device
            self.cooldown_input.text = ''
            self.cooldown_status.text = self._needs_device
            self.pulse_gap_input.text = ''
            self.pulse_gap_status.text = self._needs_device
            return
        self.button_timer.interval = 0
        try:
            result = anvil.server.call('display_status', key)
            if key is None:
                self.status.text = (
                    'Pico online' if result['online'] else 'Pico offline / not connected'
                ) if result['ok'] else result['message']
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
                    self._settings_key = ''
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
            signature = key
            if signature != self._battery_chart_key:
                self.refresh_battery_chart()
                self._battery_chart_key = signature
        finally:
            self.button_timer.interval = 2


    @handle('battery_timer', 'tick')
    def refresh_battery_chart(self, **event_args):
        key = self._dashboard_key()
        if key == '':
            return
        self.battery_timer.interval = 0
        try:
            self.refresh_box_chart(key)
            self.refresh_drink_chart(key)
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
        finally:
            self.battery_timer.interval = 30




    def refresh_box_chart(self, key):
        result = anvil.server.call('daily_box_activity', key)
        if self._dashboard_key() != key:
            return
        if not result['ok']:
            self.box_chart.data = []
            self.box_chart_status.text = result['message']
            return
        self.box_chart.data = [
            {'type': 'bar', 'name': 'Door openings', 'x': result['days'],
             'y': result['openings'], 'marker': {'color': '#28543e'}},
            {'type': 'bar', 'name': 'Tilts', 'x': result['days'],
             'y': result['tilts'], 'marker': {'color': '#b66b22'}},
        ]
        self.box_chart_status.text = ('' if any(result['openings']) or any(result['tilts'])
                                      else 'No door openings or tilts in this time range.')


    def refresh_drink_chart(self, key):
        result = anvil.server.call('daily_drink_activity', key)
        if self._dashboard_key() != key:
            return
        if not result['ok']:
            self.drink_chart.data = []
            self.drink_chart_status.text = result['message']
            return
        days = result['days']
        self.drink_chart.data = [
            {'type': 'bar', 'name': 'Button presses', 'x': days, 'y': result['presses'],
             'offsetgroup': 'presses', 'marker': {'color': '#64748b'}},
            {'type': 'bar', 'name': 'Drinks', 'x': days, 'y': result['drinks'],
             'offsetgroup': 'drinks', 'marker': {'color': '#28543e'}},
            {'type': 'bar', 'name': 'Estimated mL', 'x': days, 'y': result['millilitres'],
             'offsetgroup': 'volume', 'yaxis': 'y2', 'marker': {'color': '#b66b22'}},
        ]
        missing = result['missing_delay']
        self.drink_chart_status.text = ('Volume excludes %s drink(s) without a recorded delay.' % missing
                                   if missing else '')


    @handle('pulse_gap_save', 'click')
    def save_pulse_gap(self, **event_args):
        key = self._dashboard_key()
        text = (self.pulse_gap_input.text or '').strip()
        if key == '':
            self.pulse_gap_status.text = self._needs_device
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
        key = self._dashboard_key()
        text = (self.cooldown_input.text or '').strip()
        if key == '':
            self.cooldown_status.text = self._needs_device
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
        key = self._dashboard_key()
        if key == '':
            self.waiting_message_status.text = self._needs_device
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
        key = self._dashboard_key()
        signing_key = self.firmware_key.text or ''
        if key == '':
            self.firmware_status.text = self._needs_device
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
        key = self._dashboard_key()
        self.drink_limits_save.enabled = False
        try:
            result = anvil.server.call('set_drink_limits', key, self.drink_schedule_input.text or '',
                                       self.limit_message_input.text or '', self.drink_progress_input.text or '',
                                       self.drink_checking_input.text or '')
            self.drink_limits_status.text = 'Saved. Applies to the next checked press.' if result['ok'] else result['message']
        finally:
            self.drink_limits_save.enabled = True
