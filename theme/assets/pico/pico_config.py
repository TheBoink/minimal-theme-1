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

# Create a long random secret and keep it private. Use the same value when
# publishing a firmware file in the Anvil app. Empty disables Wi-Fi updates.
OTA_SIGNING_KEY = ''

# GP27 uses a pull-up. Set to 1 if opening the door releases the switch to high.
DOOR_OPEN_LEVEL = 0

# GP26 uses a pull-down. Set to 0 only if the sensor drives GP26 low when tilted.
TILT_ACTIVE_LEVEL = 1

# Print tilt diagnostics to USB serial and skip starting the watchdog.
TILT_DEBUG = False
