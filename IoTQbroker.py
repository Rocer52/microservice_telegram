import paho.mqtt.client as mqtt
import json
import re
import logging
import config
import time
import uuid

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global client pool to reuse MQTT clients by chat_id
client_pool = {}

# Class: Simulate IoT device, send commands to IOTQueue
class Device:
    def __init__(self, name: str, device_id: str = config.DEVICE_ID, platform: str = "unknown", chat_id: str = None):
        self.name = name  # Device name
        self.device_id = device_id  # Device ID
        self.chat_id = chat_id  # Chat ID for client reuse
        self.platform = platform  # Platform (telegram or line)
        self.message_api = MessageAPI(config.IOTQUEUE_HOST, config.IOTQUEUE_PORT, platform, device_id, chat_id)  # Create MessageAPI instance

    # Function: Enable device
    def enable(self, chat_id: str = None, platform: str = "telegram") -> bool:
        return self.message_api.send_message(f"light/esp_32/{self.device_id}/command", {"command": "on", "chat_id": chat_id, "platform": platform})

    # Function: Disable device
    def disable(self, chat_id: str = None, platform: str = "telegram") -> bool:
        return self.message_api.send_message(f"light/esp_32/{self.device_id}/command", {"command": "off", "chat_id": chat_id, "platform": platform})

    # Function: Get device status
    def get_status(self, chat_id: str = None, platform: str = "telegram") -> bool:
        return self.message_api.send_message(f"light/esp_32/{self.device_id}/command", {"command": "get_status", "chat_id": chat_id, "platform": platform})

# Class: Handle MQTT message sending, connect to IOTQueue
class MessageAPI:
    def __init__(self, broker_host: str, broker_port: int, platform: str, device_id: str, chat_id: str):
        self.broker_host = broker_host  # MQTT broker host
        self.broker_port = broker_port  # MQTT broker port
        self.device_id = device_id  # Device ID
        self.platform = platform  # Platform
        self.chat_id = chat_id or "default"  # Use chat_id or default for client key

        # Reuse or create MQTT client
        if self.chat_id in client_pool:
            self.client = client_pool[self.chat_id]
            logger.info(f"Reusing MQTT client for chat_id={self.chat_id}")
        else:
            client_id = f"iotq_broker_{platform}_{self.chat_id}_{str(uuid.uuid4())[:8]}"
            self.client = mqtt.Client(client_id=client_id)  # Create new MQTT client
            self.client.on_connect = self.on_connect  # Set connect callback
            self.client.on_disconnect = self.on_disconnect  # Set disconnect callback
            self.client.reconnect_delay_set(min_delay=1, max_delay=120)  # Set auto-reconnect parameters
            client_pool[self.chat_id] = self.client
            logger.info(f"Created new MQTT client for chat_id={self.chat_id}, client_id={client_id}")
            self.connect()  # Connect to MQTT broker

    # Callback: Handle successful connection
    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info(f"Client {client._client_id} connected to IOTQueue broker")
        else:
            logger.error(f"Client {client._client_id} failed to connect to IOTQueue broker, return code: {rc}")

    # Callback: Handle disconnection
    def on_disconnect(self, client, userdata, rc):
        logger.warning(f"Client {client._client_id} disconnected from IOTQueue broker, attempting to reconnect...")

    # Function: Connect to MQTT broker
    def connect(self):
        try:
            self.client.connect(self.broker_host, self.broker_port)  # Connect to MQTT broker
            self.client.loop_start()  # Start MQTT client loop
        except Exception as e:
            logger.error(f"Client {self.client._client_id} failed to connect to MQTT broker: {e}")
            time.sleep(5)  # Wait 5 seconds before retry
            self.connect()

    # Function: Send message to specified topic
    def send_message(self, topic: str, message: dict) -> bool:
        try:
            self.client.publish(topic, json.dumps(message))  # Send message
            logger.info(f"IOTQueue message sent successfully: topic={topic}, message={json.dumps(message)}")
            return True
        except Exception as e:
            logger.error(f"Failed to send IOTQueue message: {e}")
            return False

    # Function: Stop MQTT client
    def stop(self):
        self.client.loop_stop()  # Stop loop
        self.client.disconnect()  # Disconnect

# Function: Parse user message and convert to IoT command
def IoTParse_Message(message_text: str, device: Device, chat_id: str, platform: str = "telegram") -> dict:
    message_text = message_text.lower().strip()  # Convert to lowercase and trim
    logger.info(f"Parsing IoT message: {message_text}")

    # Match enable command
    if re.match(r"^/enable(\s+.*)?$", message_text) or re.match(r"^(turn on the light|turn on)(\s+.*)?$", message_text):
        if device.enable(chat_id, platform):
            return {"success": True, "action": "Enable"}
        else:
            return {"success": False, "message": "Failed to enable device"}
    # Match disable command
    elif re.match(r"^/disable(\s+.*)?$", message_text) or re.match(r"^(turn off the light|turn off)(\s+.*)?$", message_text):
        if device.disable(chat_id, platform):
            return {"success": True, "action": "Disable"}
        else:
            return {"success": False, "message": "Failed to disable device"}
    # Match get status command
    elif re.match(r"^/status(\s+.*)?$", message_text) or re.match(r"^(get status)(\s+.*)?$", message_text):
        if device.get_status(chat_id, platform):
            return {"success": True, "action": "GetStatus"}
        else:
            return {"success": False, "message": "Failed to get device status"}
    return {"success": False, "message": "Invalid command"}