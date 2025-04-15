from flask import Flask, request
import paho.mqtt.client as mqtt
import pika
import json
import config
import threading
import logging
from IoTQbroker import Device, IOTQueueMessageAPI
import IMQbroker

# 設置日誌
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 初始化 IOTQueue (MQTT) 客戶端
iotqueue_client = mqtt.Client()
try:
    iotqueue_client.connect(config.IOTQUEUE_HOST, config.IOTQUEUE_PORT, 60)
    iotqueue_client.loop_start()
except Exception as e:
    logger.error(f"IOTQueue 連線失敗: {e}")
    exit(1)

# 創建設備實例，用於處理 IoT 命令
message_api = IOTQueueMessageAPI(iotqueue_client)
device = Device(message_api)

# 初始化 RabbitMQ 連線，用於 IMQueue
try:
    rabbitmq_connection = pika.BlockingConnection(
        pika.ConnectionParameters(host=config.RABBITMQ_HOST, port=config.RABBITMQ_PORT)
    )
    rabbitmq_channel = rabbitmq_connection.channel()
    rabbitmq_channel.queue_declare(queue=config.RABBITMQ_QUEUE)
except Exception as e:
    logger.error(f"RabbitMQ 連線失敗: {e}")
    exit(1)

# 用於動態管理用戶和群組 ID
chat_ids = set()
group_ids = set()

# 添加 chat_id 到集合
def add_chat_id(chat_id: str):
    if chat_id:
        chat_ids.add(chat_id)

# 添加 group_id 到集合
def add_group_id(group_id: str):
    if group_id:
        group_ids.add(group_id)

# 處理 LINE Webhook 請求，將消息發送到 IMQueue
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        if data is None:
            logger.error("Webhook 請求無法解析為 JSON")
            return {"ok": False, "message": "Invalid JSON"}, 400
        
        logger.info(f"收到 Webhook 數據: {data}")
        
        if 'events' not in data or not data['events']:
            logger.warning("Webhook 請求中缺少 events，忽略此更新")
            return {"ok": True, "message": "No events in request, ignored"}, 200
        
        event = data['events'][0]  # 假設處理第一個事件
        if event.get('type') != 'message' or event.get('message', {}).get('type') != 'text':
            logger.warning("Webhook 事件不是文字消息，忽略此更新")
            return {"ok": True, "message": "Not a text message, ignored"}, 200
        
        message_text = event['message'].get('text', '')
        source = event.get('source', {})
        chat_id = source.get('userId')
        group_id = source.get('groupId') if source.get('type') == 'group' else None

        if not chat_id:
            logger.error("無法提取 chat_id")
            return {"ok": False, "message": "Cannot extract chat_id"}, 400

        add_chat_id(chat_id)
        if group_id:
            add_group_id(group_id)

        message = {"message_text": message_text, "chat_id": chat_id, "group_id": group_id, "platform": "line"}
        rabbitmq_channel.basic_publish(
            exchange='',
            routing_key=config.RABBITMQ_QUEUE,
            body=json.dumps(message)
        )
        logger.info(f"已將訊息發送到 IM Queue: {message}")
        return {"ok": True}, 200
    except Exception as e:
        logger.error(f"處理 Webhook 失敗: {e}")
        return {"ok": False, "message": str(e)}, 500

# 發送單人訊息，將請求發送到 IMQueue
@app.route('/sendMsg', methods=['GET'])
def send_msg():
    chat_id = request.args.get('chat_id')
    text = request.args.get('text', 'Hello!')
    if not chat_id:
        return "Missing chat_id", 400
    
    message = {"message_text": "/sendmsg", "chat_id": chat_id, "text": text, "platform": "line"}
    rabbitmq_channel.basic_publish(
        exchange='',
        routing_key=config.RABBITMQ_QUEUE,
        body=json.dumps(message)
    )
    logger.info(f"已將訊息發送到 IM Queue: {message}")
    return "Message queued", 200

# 發送群組訊息，將請求發送到 IMQueue
@app.route('/sendGroupMsg', methods=['GET'])
def send_group_msg():
    group_id = request.args.get('group_id')
    text = request.args.get('text', 'Hello Group!')
    if not group_id:
        return "Missing group_id", 400
    
    message = {"message_text": "/sendgroupmsg", "group_id": group_id, "text": text, "platform": "line"}
    rabbitmq_channel.basic_publish(
        exchange='',
        routing_key=config.RABBITMQ_QUEUE,
        body=json.dumps(message)
    )
    logger.info(f"已將訊息發送到 IM Queue: {message}")
    return "Group message queued", 200

# 廣播訊息，將請求發送到 IMQueue
@app.route('/sendAllMsg', methods=['GET'])
def send_all_msg():
    text = request.args.get('text', 'Broadcast message!')
    
    message = {"message_text": "/sendallmsg", "text": text, "platform": "line"}
    rabbitmq_channel.basic_publish(
        exchange='',
        routing_key=config.RABBITMQ_QUEUE,
        body=json.dumps(message)
    )
    logger.info(f"已將訊息發送到 IM Queue: {message}")
    return "Broadcast message queued", 200

if __name__ == "__main__":
    # 啟動 RabbitMQ 消費，使用 LINE 平台
    consumer_thread = threading.Thread(target=IMQbroker.start_consuming, args=(chat_ids, device, "line"))
    consumer_thread.daemon = True
    consumer_thread.start()
    app.run(host="0.0.0.0", port=5001)  # LINE 使用端口 5001