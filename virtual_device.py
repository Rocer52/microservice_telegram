from flask import Flask, request
import paho.mqtt.client as mqtt
import pika
import json
import config
import logging
import threading

# 設置日誌
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 虛擬設備類，模擬一個 IoT 設備
class VirtualDevice:
    def __init__(self, name: str, broker_host: str = "localhost", broker_port: int = 1883):
        self.name = name
        self.state = "off"
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.client = mqtt.Client(client_id=f"virtual_device_{name}")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

        # 初始化 RabbitMQ 連線
        try:
            self.rabbitmq_connection = pika.BlockingConnection(
                pika.ConnectionParameters(host=config.RABBITMQ_HOST, port=config.RABBITMQ_PORT)
            )
            self.rabbitmq_channel = self.rabbitmq_connection.channel()
            self.rabbitmq_channel.queue_declare(queue=config.RABBITMQ_QUEUE)
        except Exception as e:
            logger.error(f"RabbitMQ 連線失敗: {e}")
            exit(1)

    # MQTT 連線回調函數
    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info(f"{self.name} 已連接到 IOTQueue broker")
            self.client.subscribe("light/command")
        else:
            logger.error(f"{self.name} 連線失敗，錯誤碼: {rc}")
            exit(1)

    # 處理 MQTT 消息
    def on_message(self, client, userdata, msg):
        topic = msg.topic
        payload = json.loads(msg.payload.decode())
        logger.info(f"{self.name} 收到訊息 - 主題: {topic}, 內容: {payload}")

        if topic == "light/command":
            command = payload.get("command")
            chat_id = payload.get("chat_id")
            platform = payload.get("platform", "telegram")  # 獲取平台信息
            if command == "on":
                self.enable(chat_id, platform)
            elif command == "off":
                self.disable(chat_id, platform)
            elif command == "get_status":
                self.get_status(chat_id, platform)
            elif command in ["on", "off"]:
                self.set_status(command, chat_id, platform)

    # 通知設備狀態更新
    def notify_status(self, status: str, chat_id: str = None, platform: str = "telegram"):
        message = {"device_status": status, "chat_id": chat_id, "platform": platform}
        self.rabbitmq_channel.basic_publish(
            exchange='',
            routing_key=config.RABBITMQ_QUEUE,
            body=json.dumps(message)
        )
        logger.info(f"已將設備狀態發送到 IM Queue: {message}")
        self.client.publish("light/status", status)

    # 啟用設備
    def enable(self, chat_id: str = None, platform: str = "telegram"):
        self.state = "on"
        logger.info(f"{self.name} 已開啟")
        self.notify_status("enabled", chat_id, platform)
        return {"status": "success", "message": "Device enabled"}

    # 停用設備
    def disable(self, chat_id: str = None, platform: str = "telegram"):
        self.state = "off"
        logger.info(f"{self.name} 已關閉")
        self.notify_status("disabled", chat_id, platform)
        return {"status": "success", "message": "Device disabled"}

    # 設置設備狀態
    def set_status(self, status: str, chat_id: str = None, platform: str = "telegram"):
        self.state = status
        logger.info(f"{self.name} 狀態設為: {status}")
        self.notify_status(status, chat_id, platform)
        return {"status": "success", "message": f"Device status set to {status}"}

    # 獲取設備狀態
    def get_status(self, chat_id: str = None, platform: str = "telegram"):
        logger.info(f"{self.name} 回應狀態: {self.state}")
        self.notify_status(self.state, chat_id, platform)
        return {"status": "success", "message": self.state}

    # 啟動 MQTT 客戶端
    def start_mqtt(self):
        try:
            self.client.connect(self.broker_host, self.broker_port, 60)
            self.client.loop_start()
            logger.info(f"{self.name} MQTT 已啟動，等待訊息...")
        except Exception as e:
            logger.error(f"{self.name} MQTT 啟動失敗: {e}")
            exit(1)

    # 停止設備
    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()
        self.rabbitmq_connection.close()
        logger.info(f"{self.name} 已停止")

# 創建虛擬設備實例
device = VirtualDevice("LivingRoomLight")

# 驗證平台參數是否有效
def validate_platform(platform: str) -> bool:
    return platform in ["line", "telegram"]

# 將 Enable 命令發送到 IOTQueue
@app.route('/Enable', methods=['GET'])
def enable():
    chat_id = request.args.get('chat_id')
    platform = request.args.get('platform')
    
    # 檢查 platform 參數是否提供且有效
    if not platform:
        return {"status": "error", "message": "Missing platform parameter"}, 400
    if not validate_platform(platform):
        return {"status": "error", "message": "Invalid platform, must be 'line' or 'telegram'"}, 400

    device.client.publish("light/command", json.dumps({"command": "on", "chat_id": chat_id, "platform": platform}))
    return {"status": "queued", "message": f"Enable command queued for platform: {platform}"}, 200

# 將 Disable 命令發送到 IOTQueue
@app.route('/Disable', methods=['GET'])
def disable():
    chat_id = request.args.get('chat_id')
    platform = request.args.get('platform')
    
    # 檢查 platform 參數是否提供且有效
    if not platform:
        return {"status": "error", "message": "Missing platform parameter"}, 400
    if not validate_platform(platform):
        return {"status": "error", "message": "Invalid platform, must be 'line' or 'telegram'"}, 400

    device.client.publish("light/command", json.dumps({"command": "off", "chat_id": chat_id, "platform": platform}))
    return {"status": "queued", "message": f"Disable command queued for platform: {platform}"}, 200

# 將 SetStatus 命令發送到 IOTQueue
@app.route('/SetStatus', methods=['GET'])
def set_status():
    status = request.args.get('status')
    chat_id = request.args.get('chat_id')
    platform = request.args.get('platform')
    
    # 檢查 platform 參數是否提供且有效
    if not platform:
        return {"status": "error", "message": "Missing platform parameter"}, 400
    if not validate_platform(platform):
        return {"status": "error", "message": "Invalid platform, must be 'line' or 'telegram'"}, 400
    
    if not status:
        return {"status": "error", "message": "Missing status parameter"}, 400
    if status not in ["on", "off"]:
        return {"status": "error", "message": "Invalid status"}, 400
    
    device.client.publish("light/command", json.dumps({"command": status, "chat_id": chat_id, "platform": platform}))
    return {"status": "queued", "message": f"SetStatus command queued: {status} for platform: {platform}"}, 200

# 將 GetStatus 命令發送到 IOTQueue
@app.route('/GetStatus', methods=['GET'])
def get_status():
    chat_id = request.args.get('chat_id')
    platform = request.args.get('platform')
    
    # 檢查 platform 參數是否提供且有效
    if not platform:
        return {"status": "error", "message": "Missing platform parameter"}, 400
    if not validate_platform(platform):
        return {"status": "error", "message": "Invalid platform, must be 'line' or 'telegram'"}, 400

    device.client.publish("light/command", json.dumps({"command": "get_status", "chat_id": chat_id, "platform": platform}))
    return {"status": "queued", "message": f"GetStatus command queued for platform: {platform}"}, 200

if __name__ == "__main__":
    mqtt_thread = threading.Thread(target=device.start_mqtt)
    mqtt_thread.daemon = True
    mqtt_thread.start()
    app.run(host="0.0.0.0", port=5002)  # 虛擬設備使用端口 5002