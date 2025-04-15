from abc import ABC, abstractmethod
import re
import logging
import json
import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 定義消息 API 的抽象基類
class MessageAPI(ABC):
    @abstractmethod
    def send_message(self, topic: str, message: dict) -> bool:
        pass

# IOTQueue 消息 API，基於 MQTT 實現
class IOTQueueMessageAPI(MessageAPI):
    def __init__(self, client):
        self.client = client
        self.callbacks = {}

    # 發送消息到指定 MQTT 主題
    def send_message(self, topic: str, message: dict) -> bool:
        try:
            self.client.publish(topic, json.dumps(message))
            logger.info(f"IOTQueue 訊息發送成功: topic={topic}, message={json.dumps(message)}")
            return True
        except Exception as e:
            logger.error(f"IOTQueue 發佈失敗: {e}")
            return False

    # 接收並處理 MQTT 消息
    def receive_message(self, topic: str, callback) -> bool:
        try:
            self.callbacks[topic] = callback
            self.client.subscribe(topic)
            def on_message(client, userdata, msg):
                topic = msg.topic
                if topic in self.callbacks:
                    self.callbacks[topic](topic, msg.payload.decode())
            self.client.on_message = on_message
            logger.info(f"IOTQueue 訂閱成功: topic={topic}")
            return True
        except Exception as e:
            logger.error(f"IOTQueue 訂閱失敗: {e}")
            return False

# 設備類，負責處理 IoT 命令
class Device:
    def __init__(self, message_api: MessageAPI):
        self.message_api = message_api

    # 啟用設備
    def enable(self, chat_id: str = None, platform: str = "telegram") -> bool:
        return self.message_api.send_message("light/command", {"command": "on", "chat_id": chat_id, "platform": platform})

    # 停用設備
    def disable(self, chat_id: str = None, platform: str = "telegram") -> bool:
        return self.message_api.send_message("light/command", {"command": "off", "chat_id": chat_id, "platform": platform})

    # 設置設備狀態
    def set_status(self, status: str, chat_id: str = None, platform: str = "telegram") -> bool:
        return self.message_api.send_message("light/command", {"command": status, "chat_id": chat_id, "platform": platform})

    # 獲取設備狀態
    def get_status(self, chat_id: str = None, platform: str = "telegram") -> bool:
        return self.message_api.send_message("light/command", {"command": "get_status", "chat_id": chat_id, "platform": platform})

# 驗證設備是否有效
def authenticate_iot(device: Device) -> bool:
    if device is None:
        logger.error("認證失敗：無效的設備")
        return False
    return True

# 解析 IoT 訊息並執行對應操作
def IoTParse_Message(message_text: str, device: Device, chat_id: str, platform: str = "telegram") -> dict:
    if not authenticate_iot(device):
        return {"success": False, "message": "Device authentication failed"}

    message_text = message_text.lower().strip()
    logger.info(f"解析 IoT 訊息: {message_text}")

    if re.match(r"^/enable(\s+.*)?$", message_text) or re.match(r"^(turn on the light|turn on)(\s+.*)?$", message_text):
        if device.enable(chat_id, platform):
            return {"success": True, "action": "Enable"}
        else:
            return {"success": False, "message": "Failed to enable device"}
    
    elif re.match(r"^/disable(\s+.*)?$", message_text) or re.match(r"^(turn off the light|turn off)(\s+.*)?$", message_text):
        if device.disable(chat_id, platform):
            return {"success": True, "action": "Disable"}
        else:
            return {"success": False, "message": "Failed to disable device"}
    
    elif re.match(r"^/setstatus\s+(on|off)(\s+.*)?$", message_text):
        status = re.search(r"/setstatus\s+(on|off)", message_text).group(1)
        if device.set_status(status, chat_id, platform):
            return {"success": True, "action": "SetStatus"}
        else:
            return {"success": False, "message": f"Failed to set status to {status}"}
    
    elif re.match(r"^/getstatus(\s+.*)?$", message_text) or re.match(r"^(get status)(\s+.*)?$", message_text):
        if device.get_status(chat_id, platform):
            return {"success": True, "action": "GetStatus"}
        else:
            return {"success": False, "message": "Failed to get status"}
    
    else:
        return {"success": False, "message": "Unknown IoT command"}