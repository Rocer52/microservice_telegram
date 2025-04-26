import paho.mqtt.client as mqtt
import json
import re
import logging
import config
import time
import uuid

# 設置日誌，記錄程式執行資訊
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 類：模擬 IoT 設備，負責發送命令到 IOTQueue
class Device:
    def __init__(self, name: str, broker_host: str = "localhost", broker_port: int = 1883, platform: str = "unknown"):
        self.name = name  # 設備名稱
        self.message_api = MessageAPI(broker_host, broker_port, platform)  # 創建 MessageAPI 實例

    # 函數：啟用設備
    def enable(self, chat_id: str = None, platform: str = "telegram") -> bool:
        return self.message_api.send_message("light/command", {"command": "on", "chat_id": chat_id, "platform": platform})

    # 函數：禁用設備
    def disable(self, chat_id: str = None, platform: str = "telegram") -> bool:
        return self.message_api.send_message("light/command", {"command": "off", "chat_id": chat_id, "platform": platform})

    # 函數：獲取設備狀態
    def get_status(self, chat_id: str = None, platform: str = "telegram") -> bool:
        return self.message_api.send_message("light/command", {"command": "get_status", "chat_id": chat_id, "platform": platform})

# 類：處理 MQTT 消息發送，連接到 IOTQueue
class MessageAPI:
    def __init__(self, broker_host: str, broker_port: int, platform: str):
        # 動態生成唯一的 client_id，避免衝突
        self.client_id = f"iotq_broker_{platform}_{str(uuid.uuid4())[:8]}"
        self.broker_host = broker_host  # MQTT 代理主機
        self.broker_port = broker_port  # MQTT 代理端口
        self.client = mqtt.Client(client_id=self.client_id)  # 創建 MQTT 客戶端
        self.client.on_connect = self.on_connect  # 設置連線回調
        self.client.on_disconnect = self.on_disconnect  # 設置斷線回調
        self.client.reconnect_delay_set(min_delay=1, max_delay=120)  # 設置自動重連參數
        self.connect()  # 連接到 MQTT 代理

    # 回調函數：處理連線成功
    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info(f"客戶端 {self.client_id} 已連接到 IOTQueue 代理")
        else:
            logger.error(f"客戶端 {self.client_id} 連接到 IOTQueue 代理失敗，返回碼: {rc}")

    # 回調函數：處理斷線事件
    def on_disconnect(self, client, userdata, rc):
        logger.warning(f"客戶端 {self.client_id} 與 IOTQueue 代理斷開連線，嘗試重連...")

    # 函數：連接到 MQTT 代理
    def connect(self):
        try:
            self.client.connect(self.broker_host, self.broker_port)  # 連接到 MQTT 代理（使用預設 Keep Alive 60 秒）
            self.client.loop_start()  # 啟動 MQTT 客戶端迴圈
        except Exception as e:
            logger.error(f"客戶端 {self.client_id} 連接到 MQTT 代理失敗: {e}")
            time.sleep(5)  # 等待 5 秒後重試
            self.connect()

    # 函數：發送消息到指定主題
    def send_message(self, topic: str, message: dict) -> bool:
        try:
            self.client.publish(topic, json.dumps(message))  # 發送消息
            logger.info(f"IOTQueue 消息發送成功: topic={topic}, message={json.dumps(message)}")
            return True
        except Exception as e:
            logger.error(f"發送 IOTQueue 消息失敗: {e}")
            return False

    # 函數：停止 MQTT 客戶端
    def stop(self):
        self.client.loop_stop()  # 停止迴圈
        self.client.disconnect()  # 斷開連線

# 函數：解析用戶消息並轉換為 IoT 命令
def IoTParse_Message(message_text: str, device: Device, chat_id: str, platform: str = "telegram") -> dict:
    message_text = message_text.lower().strip()  # 轉為小寫並去除空白
    logger.info(f"正在解析 IoT 消息: {message_text}")

    # 匹配啟用命令
    if re.match(r"^/enable(\s+.*)?$", message_text) or re.match(r"^(turn on the light|turn on)(\s+.*)?$", message_text):
        if device.enable(chat_id, platform):
            return {"success": True, "action": "Enable"}
        else:
            return {"success": False, "message": "無法啟用設備"}
    # 匹配禁用命令
    elif re.match(r"^/disable(\s+.*)?$", message_text) or re.match(r"^(turn off the light|turn off)(\s+.*)?$", message_text):
        if device.disable(chat_id, platform):
            return {"success": True, "action": "Disable"}
        else:
            return {"success": False, "message": "無法禁用設備"}
    # 匹配獲取狀態命令
    elif re.match(r"^/status(\s+.*)?$", message_text) or re.match(r"^(get status)(\s+.*)?$", message_text):
        if device.get_status(chat_id, platform):
            return {"success": True, "action": "GetStatus"}
        else:
            return {"success": False, "message": "無法獲取設備狀態"}
    return {"success": False, "message": "無效的命令"}