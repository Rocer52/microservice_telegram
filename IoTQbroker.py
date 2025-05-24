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

# Global bindings dictionary to store user-device bindings
bindings = {}  # Format: {device_id: set(chat_id)}

# Class: Simulate IoT device, send commands to IOTQueue
class Device:
    def __init__(self, name: str, device_id: str = config.DEVICE_ID, platform: str = "unknown", chat_id: str = None):
        self.name = name
        self.device_id = device_id
        self.chat_id = chat_id
        self.platform = platform
        # Determine manufacturer based on device_id
        self.manufacturer = "raspberrypi" if "raspberrypi" in device_id else "esp32"
        self.device_type = "light" if "light" in device_id else "fan"
        logger.info(f"Initialized Device: device_id={device_id}, manufacturer={self.manufacturer}, device_type={self.device_type}")
        try:
            self.message_api = MessageAPI(config.IOTQUEUE_HOST, config.IOTQUEUE_PORT, platform, device_id, chat_id)
        except Exception as e:
            logger.error(f"Failed to initialize MessageAPI for device {device_id}: {e}")
            raise

    # Function: Bind user to this device
    def bind_user(self, chat_id: str) -> bool:
        try:
            if self.device_id not in bindings:
                bindings[self.device_id] = set()
            bindings[self.device_id].add(chat_id)
            logger.info(f"User {chat_id} bound to device {self.device_id}. Current bindings: {bindings}")
            return True
        except Exception as e:
            logger.error(f"Failed to bind user {chat_id} to device {self.device_id}: {e}")
            return False

    # Function: Get all users bound to this device
    def get_bound_users(self) -> set:
        try:
            bound_users = bindings.get(self.device_id, set())
            logger.info(f"Retrieved bound users for device {self.device_id}: {bound_users}")
            return bound_users
        except Exception as e:
            logger.error(f"Failed to get bound users for device {self.device_id}: {e}")
            return set()

    # Function: Enable device
    def enable(self, chat_id: str = None, platform: str = "telegram") -> bool:
        topic = f"{self.manufacturer}/{self.device_type}/enable"
        message = {"command": "on", "chat_id": chat_id, "platform": platform, "device_id": self.device_id}
        try:
            logger.info(f"Sending enable command: topic={topic}, message={json.dumps(message)}")
            return self.message_api.send_message(topic, message)
        except Exception as e:
            logger.error(f"Failed to enable device {self.device_id} on topic {topic}: {e}")
            return False

    # Function: Disable device
    def disable(self, chat_id: str = None, platform: str = "telegram") -> bool:
        topic = f"{self.manufacturer}/{self.device_type}/disable"
        message = {"command": "off", "chat_id": chat_id, "platform": platform, "device_id": self.device_id}
        try:
            logger.info(f"Sending disable command: topic={topic}, message={json.dumps(message)}")
            return self.message_api.send_message(topic, message)
        except Exception as e:
            logger.error(f"Failed to disable device {self.device_id} on topic {topic}: {e}")
            return False

    # Function: Get device status
    def get_status(self, chat_id: str = None, platform: str = "telegram") -> bool:
        topic = f"{self.manufacturer}/{self.device_type}/get_status"
        message = {"command": "get_status", "chat_id": chat_id, "platform": platform, "device_id": self.device_id}
        try:
            logger.info(f"Sending get_status command: topic={topic}, message={json.dumps(message)}")
            return self.message_api.send_message(topic, message)
        except Exception as e:
            logger.error(f"Failed to get status for device {self.device_id} on topic {topic}: {e}")
            return False

# Class: Handle MQTT message sending, connect to production IOTQueue
class MessageAPI:
    def __init__(self, broker_host: str, broker_port: int, platform: str, device_id: str, chat_id: str):
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.device_id = device_id
        self.platform = platform
        self.chat_id = chat_id or "default"

        # Reuse or create MQTT client
        if self.chat_id in client_pool:
            self.client = client_pool[self.chat_id]
            logger.info(f"Reusing MQTT client for chat_id={self.chat_id}")
        else:
            try:
                client_id = f"iotq_broker_{platform}_{self.chat_id}_{str(uuid.uuid4())[:8]}"
                self.client = mqtt.Client(client_id=client_id)
                self.client.on_connect = self.on_connect
                self.client.on_disconnect = self.on_disconnect
                self.client.reconnect_delay_set(min_delay=1, max_delay=120)
                client_pool[self.chat_id] = self.client
                logger.info(f"Created new MQTT client for chat_id={self.chat_id}, client_id={client_id}")
                self.connect()
            except Exception as e:
                logger.error(f"Failed to create MQTT client for chat_id={self.chat_id}: {e}")
                raise

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
        max_retries = 5
        retry_count = 0
        while retry_count < max_retries:
            try:
                self.client.connect(self.broker_host, self.broker_port)
                self.client.loop_start()
                logger.info(f"Client {self.client._client_id} connected to MQTT broker")
                return
            except Exception as e:
                retry_count += 1
                logger.error(f"Client {self.client._client_id} failed to connect to MQTT broker (attempt {retry_count}/{max_retries}): {e}")
                if retry_count == max_retries:
                    raise
                time.sleep(5)

    # Function: Send message to specified topic
    def send_message(self, topic: str, message: dict) -> bool:
        try:
            self.client.publish(topic, json.dumps(message))
            logger.info(f"IOTQueue message sent successfully: topic={topic}, message={json.dumps(message)}")
            return True
        except Exception as e:
            logger.error(f"Failed to send IOTQueue message: topic={topic}, error={e}")
            return False

    # Function: Stop MQTT client
    def stop(self):
        try:
            self.client.loop_stop()
            self.client.disconnect()
            logger.info(f"Client {self.client._client_id} stopped")
        except Exception as e:
            logger.error(f"Failed to stop MQTT client {self.client._client_id}: {e}")

# Function: Parse user message and convert to IoT command
def IoTParse_Message(message_text: str, device: Device, chat_id: str, platform: str = "telegram") -> dict:
    message_text = message_text.lower().strip()
    logger.info(f"Parsing IoT message: {message_text}")

    try:
        if message_text in ["hi", "hello", "/start"]:
            help_text = (
                "It is an IoT control bot\n"
                "Use commands:\n"
                "turn on {device_id} to enable the device\n"
                "turn off {device_id} to disable the device\n"
                "get status {device_id} to get the device status\n"
                "/Bind {device_id} to bind to a device"
            )
            from IMQbroker import send_message
            send_message(chat_id, help_text, platform)
            return {"success": True, "action": "Help"}

        # Match bind command
        bind_match = re.match(r"^/bind\s+([\w_]+)$", message_text)
        if bind_match:
            device_id = bind_match.group(1)
            # Validate device_id
            if device_id not in config.SUPPORTED_DEVICES:
                from IMQbroker import send_message
                send_message(chat_id, f"Invalid device_id: {device_id}. Available devices: {', '.join(config.SUPPORTED_DEVICES)}", platform)
                return {"success": False, "message": "Invalid device_id"}
            target_device = Device(device.name, device_id=device_id, platform=platform, chat_id=chat_id)
            if target_device.bind_user(chat_id):
                from IMQbroker import send_message
                send_message(chat_id, f"Successfully bound to device {device_id}", platform)
                return {"success": True, "action": "Bind", "device_id": device_id}
            else:
                from IMQbroker import send_message
                send_message(chat_id, f"Failed to bind to device {device_id}", platform)
                return {"success": False, "message": "Failed to bind to device"}

        # Existing command matching logic
        enable_match = re.match(r"^(turn on|/enable)(\s+([\w_]+))?$", message_text)
        disable_match = re.match(r"^(turn off|/disable)(\s+([\w_]+))?$", message_text)
        status_match = re.match(r"^(get status|/status)(\s+([\w_]+))?$", message_text)

        device_id = None
        if enable_match and enable_match.group(3):
            device_id = enable_match.group(3)
        elif disable_match and disable_match.group(3):
            device_id = disable_match.group(3)
        elif status_match and status_match.group(3):
            device_id = status_match.group(3)
        else:
            device_id = device.device_id

        # Validate device_id
        if device_id not in config.SUPPORTED_DEVICES:
            from IMQbroker import send_message
            send_message(chat_id, f"Invalid device_id: {device_id}. Available devices: {', '.join(config.SUPPORTED_DEVICES)}", platform)
            return {"success": False, "message": "Invalid device_id"}

        target_device = Device(device.name, device_id=device_id, platform=platform, chat_id=chat_id)

        from IMQbroker import send_message
        if enable_match:
            if target_device.enable(chat_id, platform):
                send_message(chat_id, f"Command received: turn on {device_id}", platform)
                return {"success": True, "action": "Enable", "device_id": device_id}
            else:
                send_message(chat_id, f"Failed to enable device {device_id}", platform)
                return {"success": False, "message": "Failed to enable device"}
        elif disable_match:
            if target_device.disable(chat_id, platform):
                send_message(chat_id, f"Command received: turn off {device_id}", platform)
                return {"success": True, "action": "Disable", "device_id": device_id}
            else:
                send_message(chat_id, f"Failed to disable device {device_id}", platform)
                return {"success": False, "message": "Failed to disable device"}
        elif status_match:
            if target_device.get_status(chat_id, platform):
                send_message(chat_id, f"Command received: get status {device_id}", platform)
                return {"success": True, "action": "GetStatus", "device_id": device_id}
            else:
                send_message(chat_id, f"Failed to get status for device {device_id}", platform)
                return {"success": False, "message": "Failed to get device status"}
        else:
            send_message(chat_id, "Invalid command. Use /start for help.", platform)
            return {"success": False, "message": "Invalid command"}
    except Exception as e:
        logger.error(f"Error parsing message '{message_text}' for chat_id={chat_id}: {e}", exc_info=True)
        from IMQbroker import send_message
        send_message(chat_id, "An error occurred while processing your command. Please try again.", platform)
        return {"success": False, "message": "Error processing command"}

if __name__ == "__main__":
    # Example usage (for testing)
    device = Device("TestLight", device_id="raspberrypi_light_001")
    print(IoTParse_Message("turn off raspberrypi_light_001", device, "7890547742"))