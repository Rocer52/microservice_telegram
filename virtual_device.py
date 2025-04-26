from flask import Flask, request
import paho.mqtt.client as mqtt
import pika
import json
import config
import logging
import threading
import time

# 設置日誌，記錄程式執行資訊
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 類：模擬虛擬設備，處理 MQTT 命令並與 IMQueue 通信
class VirtualDevice:
    def __init__(self, name: str, broker_host: str = "localhost", broker_port: int = 1883):
        self.name = name  # 設備名稱
        self.state = "off"  # 設備初始狀態為關閉
        self.broker_host = broker_host  # MQTT 代理主機
        self.broker_port = broker_port  # MQTT 代理端口
        self.client = mqtt.Client(client_id=f"virtual_device_{name}")  # 創建 MQTT 客戶端
        self.client.on_connect = self.on_connect  # 設置連線回調
        self.client.on_message = self.on_message  # 設置消息回調
        self.client.on_disconnect = self.on_disconnect  # 設置斷線回調
        self.client.reconnect_delay_set(min_delay=1, max_delay=120)  # 設置自動重連參數

        # 初始化 RabbitMQ 連線
        self.rabbitmq_connection = None
        self.rabbitmq_channel = None
        self.connect_rabbitmq()

    # 函數：連接到 RabbitMQ 伺服器
    def connect_rabbitmq(self):
        try:
            # 設置心跳間隔為 30 秒，保持連線活躍
            parameters = pika.ConnectionParameters(
                host=config.RABBITMQ_HOST,
                port=config.RABBITMQ_PORT,
                heartbeat=30
            )
            self.rabbitmq_connection = pika.BlockingConnection(parameters)  # 連接到 RabbitMQ
            self.rabbitmq_channel = self.rabbitmq_connection.channel()  # 創建通道
            self.rabbitmq_channel.queue_declare(queue=config.RABBITMQ_QUEUE)  # 聲明隊列
            logger.info(f"已連接到 RabbitMQ: host={config.RABBITMQ_HOST}, port={config.RABBITMQ_PORT}")
        except Exception as e:
            logger.error(f"連接到 RabbitMQ 失敗: {e}")
            time.sleep(5)  # 等待 5 秒後重試
            self.connect_rabbitmq()

    # 回調函數：處理 MQTT 連線成功
    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info(f"{self.name} 已連接到 IOTQueue 代理")
            self.client.subscribe("light/command")  # 訂閱 light/command 主題
        else:
            logger.error(f"{self.name} 連線失敗，返回碼: {rc}")

    # 回調函數：處理 MQTT 斷線事件
    def on_disconnect(self, client, userdata, rc):
        logger.warning(f"{self.name} 與 IOTQueue 代理斷開連線，嘗試重連...")

    # 回調函數：處理接收到的 MQTT 消息
    def on_message(self, client, userdata, msg):
        topic = msg.topic  # 獲取消息主題
        payload = json.loads(msg.payload.decode())  # 解析消息內容
        logger.info(f"{self.name} 收到消息 - 主題: {topic}, 內容: {payload}")

        if topic == "light/command":  # 處理 light/command 主題的消息
            command = payload.get("command")  # 獲取命令
            chat_id = payload.get("chat_id")  # 獲取聊天 ID
            platform = payload.get("platform", "telegram")  # 獲取平台
            if command == "on":
                self.enable(chat_id, platform)  # 啟用設備
            elif command == "off":
                self.disable(chat_id, platform)  # 禁用設備
            elif command == "get_status":
                self.get_status(chat_id, platform)  # 獲取設備狀態
            elif command in ["on", "off"]:
                self.set_status(command, chat_id, platform)  # 設置設備狀態

    # 函數：通知設備狀態，發送到 IMQueue 和 MQTT
    def notify_status(self, status: str, chat_id: str = None, platform: str = "telegram"):
        message = {"device_status": status, "chat_id": chat_id, "platform": platform}  # 構建消息
        try:
            self.rabbitmq_channel.basic_publish(
                exchange='',
                routing_key=config.RABBITMQ_QUEUE,
                body=json.dumps(message)
            )  # 發送到 IMQueue
            logger.info(f"已發送設備狀態到 IM Queue: {message}")
            self.client.publish("light/status", status)  # 發送到 MQTT 主題
        except (pika.exceptions.StreamLostError, pika.exceptions.ConnectionClosed) as e:
            logger.warning(f"RabbitMQ 連線斷開: {e}，嘗試重連...")
            self.connect_rabbitmq()  # 重連 RabbitMQ
            # 再次發送消息
            self.rabbitmq_channel.basic_publish(
                exchange='',
                routing_key=config.RABBITMQ_QUEUE,
                body=json.dumps(message)
            )
            logger.info(f"重連後發送設備狀態到 IM Queue: {message}")
            self.client.publish("light/status", status)  # 再次發送到 MQTT 主題

    # 函數：啟用設備
    def enable(self, chat_id: str = None, platform: str = "telegram"):
        self.state = "on"  # 設置狀態為開啟
        logger.info(f"{self.name} 已啟用")
        self.notify_status("enabled", chat_id, platform)  # 通知狀態
        return {"status": "success", "message": "設備已啟用"}

    # 函數：禁用設備
    def disable(self, chat_id: str = None, platform: str = "telegram"):
        self.state = "off"  # 設置狀態為關閉
        logger.info(f"{self.name} 已禁用")
        self.notify_status("disabled", chat_id, platform)  # 通知狀態
        return {"status": "success", "message": "設備已禁用"}

    # 函數：設置設備狀態
    def set_status(self, status: str, chat_id: str = None, platform: str = "telegram"):
        self.state = status  # 設置設備狀態
        logger.info(f"{self.name} 狀態設為: {status}")
        self.notify_status(status, chat_id, platform)  # 通知狀態
        return {"status": "success", "message": f"設備狀態設為 {status}"}

    # 函數：獲取設備狀態
    def get_status(self, chat_id: str = None, platform: str = "telegram"):
        logger.info(f"{self.name} 回應狀態: {self.state}")
        self.notify_status(self.state, chat_id, platform)  # 通知當前狀態
        return {"status": "success", "message": self.state}

    # 函數：啟動 MQTT 客戶端
    def start_mqtt(self):
        try:
            self.client.connect(self.broker_host, self.broker_port)  # 連接到 MQTT 代理（使用預設 Keep Alive 60 秒）
            self.client.loop_start()  # 啟動 MQTT 客戶端迴圈
            logger.info(f"{self.name} MQTT 已啟動，等待消息...")
        except Exception as e:
            logger.error(f"{self.name} MQTT 啟動失敗: {e}")
            time.sleep(5)  # 等待 5 秒後重試
            self.start_mqtt()

    # 函數：停止設備
    def stop(self):
        self.client.loop_stop()  # 停止 MQTT 客戶端迴圈
        self.client.disconnect()  # 斷開 MQTT 連線
        if self.rabbitmq_connection and not self.rabbitmq_connection.is_closed:
            self.rabbitmq_connection.close()  # 關閉 RabbitMQ 連線
        logger.info(f"{self.name} 已停止")

# 創建設備實例
device = VirtualDevice("LivingRoomLight")

# 函數：驗證平台參數
def validate_platform(platform: str) -> bool:
    return platform in ["line", "telegram"]  # 確保平台為 line 或 telegram

# 路由：啟用設備 API
@app.route('/Enable', methods=['GET'])
def enable():
    chat_id = request.args.get('chat_id')  # 獲取聊天 ID
    platform = request.args.get('platform')  # 獲取平台
    if not platform:
        return {"status": "error", "message": "缺少 platform 參數"}, 400
    if not validate_platform(platform):
        return {"status": "error", "message": "無效的 platform，必須是 'line' 或 'telegram'"}, 400

    device.client.publish("light/command", json.dumps({"command": "on", "chat_id": chat_id, "platform": platform}))  # 發送啟用命令
    return {"status": "queued", "message": f"啟用命令已排隊，平台: {platform}"}, 200

# 路由：禁用設備 API
@app.route('/Disable', methods=['GET'])
def disable():
    chat_id = request.args.get('chat_id')  # 獲取聊天 ID
    platform = request.args.get('platform')  # 獲取平台
    if not platform:
        return {"status": "error", "message": "缺少 platform 參數"}, 400
    if not validate_platform(platform):
        return {"status": "error", "message": "無效的 platform，必須是 'line' 或 'telegram'"}, 400

    device.client.publish("light/command", json.dumps({"command": "off", "chat_id": chat_id, "platform": platform}))  # 發送禁用命令
    return {"status": "queued", "message": f"禁用命令已排隊，平台: {platform}"}, 200

# 路由：設置設備狀態 API
@app.route('/SetStatus', methods=['GET'])
def set_status():
    status = request.args.get('status')  # 獲取狀態
    chat_id = request.args.get('chat_id')  # 獲取聊天 ID
    platform = request.args.get('platform')  # 獲取平台
    if not platform:
        return {"status": "error", "message": "缺少 platform 參數"}, 400
    if not validate_platform(platform):
        return {"status": "error", "message": "無效的 platform，必須是 'line' 或 'telegram'"}, 400
    if not status:
        return {"status": "error", "message": "缺少 status 參數"}, 400
    if status not in ["on", "off"]:
        return {"status": "error", "message": "無效的 status"}, 400
    
    device.client.publish("light/command", json.dumps({"command": status, "chat_id": chat_id, "platform": platform}))  # 發送設置狀態命令
    return {"status": "queued", "message": f"設置狀態命令已排隊: {status}，平台: {platform}"}, 200

# 路由：獲取設備狀態 API
@app.route('/GetStatus', methods=['GET'])
def get_status():
    chat_id = request.args.get('chat_id')  # 獲取聊天 ID
    platform = request.args.get('platform')  # 獲取平台
    if not platform:
        return {"status": "error", "message": "缺少 platform 參數"}, 400
    if not validate_platform(platform):
        return {"status": "error", "message": "無效的 platform，必須是 'line' 或 'telegram'"}, 400

    device.client.publish("light/command", json.dumps({"command": "get_status", "chat_id": chat_id, "platform": platform}))  # 發送獲取狀態命令
    return {"status": "queued", "message": f"獲取狀態命令已排隊，平台: {platform}"}, 200

# 主程式入口
if __name__ == "__main__":
    # 啟動 MQTT 客戶端線程
    mqtt_thread = threading.Thread(target=device.start_mqtt)
    mqtt_thread.daemon = True  # 設置為守護線程，隨主程式結束
    mqtt_thread.start()
    # 啟動 Flask 服務，監聽在 0.0.0.0:5002
    app.run(host="0.0.0.0", port=5002)