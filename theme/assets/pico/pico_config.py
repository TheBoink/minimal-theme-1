"""Copy to the Pico as pico_config.py and edit privately on your own device."""

# Copy these four values from your existing main.py or the app.
WIFI_SSID = 'YOUR_WIFI_NAME'
WIFI_PASSWORD = 'YOUR_WIFI_PASSWORD'
# Use the complete HTTPS Device polling endpoint shown in the published app.
MESSAGE_URL = 'https://YOUR_APP.anvil.app/device/message'
PAIRING_KEY = 'YOUR_PAIRING_KEY'

# Optional timing settings. The HTTP timeout must be shorter than the watchdog.
POLL_SECONDS = 3
WATCHDOG_TIMEOUT_MS = 8000
HTTP_TIMEOUT_SECONDS = 3
