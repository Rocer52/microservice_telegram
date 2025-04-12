import os

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

if not TELEGRAM_BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN 環境變數未設定")
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

MQTT_BROKER_HOST = os.getenv('MQTT_BROKER_HOST', 'localhost')
try:
    MQTT_BROKER_PORT = int(os.getenv('MQTT_BROKER_PORT', 1883))
except ValueError:
    raise ValueError("MQTT_BROKER_PORT 必須是一個整數")

##