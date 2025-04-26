import os
# LINE 和 Telegram 的 API 配置
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')  # Telegram Bot 的 Token
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"  # Telegram API 網址
LINE_API_URL = os.getenv('LINE_API_URL', 'https://api.line.me/v2/bot/message')  # LINE API 網址
LINE_ACCESS_TOKEN = os.getenv('LINE_ACCESS_TOKEN', 'YOUR_LINE_ACCESS_TOKEN')  # LINE Bot 的 Access Token

LINE_ACCESS_TOKEN = '6rJb6v5xfB+GByowhbjpXbRIz7iNwb/MU0rJQbUjVcKVM5l5PN901F7EfJnS0fd6ZCSiBc7DEaHc/5DC4t4/pl6RuB9zQXxdW38lcdiuHToGb+nwJqHSPcVudAl3/F+x/k9blGyakcHr0+MXBVocYwdB04t89/1O/w1cDnyilFU='

# IOTQueue (MQTT) 和 RabbitMQ 的配置
IOTQUEUE_HOST = os.getenv('IOTQUEUE_HOST', 'localhost')  # IOTQueue 主機地址
IOTQUEUE_PORT = int(os.getenv('IOTQUEUE_PORT', 1883))  # IOTQueue 端口
RABBITMQ_HOST = os.getenv('RABBITMQ_HOST', 'localhost')  # RabbitMQ 主機地址
RABBITMQ_PORT = int(os.getenv('RABBITMQ_PORT', 5672))  # RabbitMQ 端口
RABBITMQ_QUEUE = "IMQueue"  # RabbitMQ 隊列名稱
