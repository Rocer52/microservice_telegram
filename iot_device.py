import paho.mqtt.client as mqtt
import pika
import json
import config
import logging
import threading
import time
import requests
from flask import Flask, request

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)

# Class: Simulate IoT device, handle MQTT commands and call device-specific API
class IoTDevice:
    def __init__(self, name: str, device_id: str, device_type: str, broker_host: str = config.IOTQUEUE_HOST, broker_port: int = config.IOTQUEUE_PORT):
        self.name = name  # Device name
        self.device_id = device_id  # Device ID
        self.device_type = device_type  # Device type (esp32 or raspberry_pi)
        self.broker_host = broker_host  # MQTT broker host
        self.broker_port = broker_port  # MQTT broker port
        self.client = mqtt.Client(client_id=f"iot_device_{name}_{device_id}_{device_type}")  # Create MQTT client
        self.client.on_connect = self.on_connect  # Set connect callback
        self.client.on_message = self.on_message  # Set message callback
        self.client.on_disconnect = self.on_disconnect  # Set disconnect callback
        self.client.reconnect_delay_set(min_delay=1, max_delay=120)  # Set auto-reconnect parameters

        # Initialize RabbitMQ connection
        self.rabbitmq_connection = None
        self.rabbitmq_channel = None
        self.connect_rabbitmq()

    # Function: Connect to RabbitMQ server
    def connect_rabbitmq(self):
        try:
            parameters = pika.ConnectionParameters(
                host=config.RABBITMQ_HOST,
                port=config.RABBITMQ_PORT,
                heartbeat=30
            )
            self.rabbitmq_connection = pika.BlockingConnection(parameters)  # Connect to RabbitMQ
            self.rabbitmq_channel = self.rabbitmq_connection.channel()  # Create channel
            self.rabbitmq_channel.queue_declare(queue=config.RABBITMQ_QUEUE, durable=True)  # Declare queue with durable=True
            logger.info(f"Connected to RabbitMQ: host={config.RABBITMQ_HOST}, port={config.RABBITMQ_PORT}")
        except Exception as e:
            logger.error(f"Failed to connect to RabbitMQ: {e}")
            time.sleep(5)  # Wait 5 seconds before retry
            self.connect_rabbitmq()

    # Callback: Handle successful MQTT connection
    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info(f"{self.name} ({self.device_type}, {self.device_id}) connected to IOTQueue broker")
            self.client.subscribe(f"{self.device_type}/light/{self.device_id}/message")  # Subscribe to command topic
        else:
            logger.error(f"{self.name} ({self.device_type}, {self.device_id}) connection failed, return code: {rc}")

    # Callback: Handle MQTT disconnection
    def on_disconnect(self, client, userdata, rc):
        logger.warning(f"{self.name} ({self.device_type}, {self.device_id}) disconnected from IOTQueue broker, attempting to reconnect...")

    # Callback: Handle received MQTT messages
    def on_message(self, client, userdata, msg):
        topic = msg.topic  # Get message topic
        payload = msg.payload.decode()  # Decode payload
        logger.info(f"{self.name} ({self.device_type}, {self.device_id}) received message - Topic: {topic}, Payload: {payload}")

        if topic == f"{self.device_type}/light/{self.device_id}/message":  # Handle command topic
            try:
                payload_dict = json.loads(payload)  # Try to parse as JSON
                command = payload_dict.get("command")  # Get command
                chat_id = payload_dict.get("chat_id")  # Get chat ID
                platform = payload_dict.get("platform", "telegram")  # Get platform
                device_id = payload_dict.get("device_id", self.device_id)  # Get device_id from payload
                if device_id != self.device_id:
                    logger.info(f"Ignoring message for device_id {device_id}, this device is {self.device_id}")
                    return
                if command == "on":
                    self.enable(chat_id, platform)  # Enable device
                elif command == "off":
                    self.disable(chat_id, platform)  # Disable device
                elif command == "get_status":
                    self.get_status(chat_id, platform)  # Get device status
                elif command in ["on", "off"]:
                    self.set_status(command, chat_id, platform)  # Set device status
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse payload as JSON: {e}, payload: {payload}")
                return

    # Function: Notify device status to IMQueue and MQTT
    def notify_status(self, status: str, chat_id: str = None, platform: str = "telegram"):
        message = {"device_status": status, "chat_id": chat_id, "platform": platform, "device_id": self.device_id}  # Build message
        try:
            self.rabbitmq_channel.basic_publish(
                exchange='',
                routing_key=config.RABBITMQ_QUEUE,
                body=json.dumps(message)
            )  # Send to IMQueue
            logger.info(f"Sent device status to IM Queue: {message}")
            self.client.publish(f"{self.device_type}/light/{self.device_id}/status", json.dumps({"status": status}))  # Send JSON status
        except (pika.exceptions.StreamLostError, pika.exceptions.ConnectionClosed) as e:
            logger.warning(f"RabbitMQ connection lost: {e}, attempting to reconnect...")
            self.connect_rabbitmq()  # Reconnect RabbitMQ
            self.rabbitmq_channel.basic_publish(
                exchange='',
                routing_key=config.RABBITMQ_QUEUE,
                body=json.dumps(message)
            )
            logger.info(f"Sent device status to IM Queue after reconnect: {message}")
            self.client.publish(f"{self.device_type}/light/{self.device_id}/status", json.dumps({"status": status}))  # Resend JSON status

    # Function: Get API base URL based on device type
    def get_api_base_url(self):
        if self.device_type == "raspberry_pi":
            return f"http://{config.ESP32_API_HOST}:{config.RASPBERRY_PI_API_PORT}"
        return f"http://{config.ESP32_API_HOST}:{config.ESP32_API_PORT}"

    # Function: Enable device, call device-specific API
    def enable(self, chat_id: str = None, platform: str = "telegram"):
        try:
            response = requests.get(f"{self.get_api_base_url()}/Enable", params={"device_id": self.device_id})
            if response.status_code == 200 and response.json().get("status") == "success":
                logger.info(f"{self.name} ({self.device_type}, {self.device_id}) enabled")
                self.notify_status("enabled", chat_id, platform)  # Notify status
                return response.json()
            else:
                logger.error(f"Failed to enable device: {response.text}")
                return {"status": "error", "message": "Failed to enable device"}
        except Exception as e:
            logger.error(f"Failed to call {self.device_type} Enable API: {e}")
            return {"status": "error", "message": "Failed to call API"}

    # Function: Disable device, call device-specific API
    def disable(self, chat_id: str = None, platform: str = "telegram"):
        try:
            response = requests.get(f"{self.get_api_base_url()}/Disable", params={"device_id": self.device_id})
            if response.status_code == 200 and response.json().get("status") == "success":
                logger.info(f"{self.name} ({self.device_type}, {self.device_id}) disabled")
                self.notify_status("disabled", chat_id, platform)  # Notify status
                return response.json()
            else:
                logger.error(f"Failed to disable device: {response.text}")
                return {"status": "error", "message": "Failed to disable device"}
        except Exception as e:
            logger.error(f"Failed to call {self.device_type} Disable API: {e}")
            return {"status": "error", "message": "Failed to call API"}

    # Function: Set device status, call device-specific API
    def set_status(self, status: str, chat_id: str = None, platform: str = "telegram"):
        try:
            response = requests.get(f"{self.get_api_base_url()}/SetStatus", params={"device_id": self.device_id, "status": status})
            if response.status_code == 200 and response.json().get("status") == "success":
                logger.info(f"{self.name} ({self.device_type}, {self.device_id}) status set to: {status}")
                self.notify_status(status, chat_id, platform)  # Notify status
                return response.json()
            else:
                logger.error(f"Failed to set device status: {response.text}")
                return {"status": "error", "message": "Failed to set device status"}
        except Exception as e:
            logger.error(f"Failed to call {self.device_type} SetStatus API: {e}")
            return {"status": "error", "message": "Failed to call API"}

    # Function: Get device status, call device-specific API
    def get_status(self, chat_id: str = None, platform: str = "telegram"):
        try:
            response = requests.get(f"{self.get_api_base_url()}/GetStatus", params={"device_id": self.device_id})
            if response.status_code == 200 and response.json().get("state") is not None:
                state = response.json().get("state")
                logger.info(f"{self.name} ({self.device_type}, {self.device_id}) status: {state}")
                self.notify_status(state, chat_id, platform)  # Notify status
                return response.json()
            else:
                logger.error(f"Failed to get device status: {response.text}")
                return {"status": "error", "message": "Failed to get device status"}
        except Exception as e:
            logger.error(f"Failed to call {self.device_type} GetStatus API: {e}")
            return {"status": "error", "message": "Failed to call API"}

    # Function: Start MQTT client
    def start_mqtt(self):
        try:
            self.client.connect(self.broker_host, self.broker_port)  # Connect to MQTT broker
            self.client.loop_start()  # Start MQTT client loop
            logger.info(f"{self.name} ({self.device_type}, {self.device_id}) MQTT started, waiting for messages...")
        except Exception as e:
            logger.error(f"{self.name} ({self.device_type}, {self.device_id}) MQTT start failed: {e}")
            time.sleep(5)  # Wait 5 seconds before retry
            self.start_mqtt()

    # Function: Stop device
    def stop(self):
        self.client.loop_stop()  # Stop MQTT client loop
        self.client.disconnect()  # Disconnect MQTT
        if self.rabbitmq_connection and not self.rabbitmq_connection.is_closed:
            self.rabbitmq_connection.close()  # Close RabbitMQ connection
        logger.info(f"{self.name} ({self.device_type}, {self.device_id}) stopped")

# Create device instances for ESP32 and Raspberry Pi
devices = [
    IoTDevice("LivingRoomLight", device_id="esp_32_001", device_type="esp32"),
    IoTDevice("LivingRoomLight", device_id="raspberry_pi_001", device_type="raspberry_pi")
]

# Flask Routes
@app.route('/Enable', methods=['GET'])
def enable_route():
    device_id = request.args.get('device_id')
    chat_id = request.args.get('chat_id')
    platform = request.args.get('platform', 'telegram')
    if not device_id:
        return {"status": "error", "message": "Missing device_id parameter"}, 400
    device = next((d for d in devices if d.device_id == device_id), None)
    if not device:
        return {"status": "error", "message": f"Device {device_id} not found"}, 404

    result = device.enable(chat_id, platform)
    return result, 200 if result.get("status") == "success" else 500

@app.route('/Disable', methods=['GET'])
def disable_route():
    device_id = request.args.get('device_id')
    chat_id = request.args.get('chat_id')
    platform = request.args.get('platform', 'telegram')
    if not device_id:
        return {"status": "error", "message": "Missing device_id parameter"}, 400
    device = next((d for d in devices if d.device_id == device_id), None)
    if not device:
        return {"status": "error", "message": f"Device {device_id} not found"}, 404

    result = device.disable(chat_id, platform)
    return result, 200 if result.get("status") == "success" else 500

@app.route('/GetStatus', methods=['GET'])
def get_status_route():
    device_id = request.args.get('device_id')
    chat_id = request.args.get('chat_id')
    platform = request.args.get('platform', 'telegram')
    if not device_id:
        return {"status": "error", "message": "Missing device_id parameter"}, 400
    device = next((d for d in devices if d.device_id == device_id), None)
    if not device:
        return {"status": "error", "message": f"Device {device_id} not found"}, 404

    result = device.get_status(chat_id, platform)
    return result, 200 if result.get("status") == "success" else 500

@app.route('/SetStatus', methods=['GET'])
def set_status_route():
    device_id = request.args.get('device_id')
    status = request.args.get('status')
    chat_id = request.args.get('chat_id')
    platform = request.args.get('platform', 'telegram')
    if not device_id:
        return {"status": "error", "message": "Missing device_id parameter"}, 400
    if not status:
        return {"status": "error", "message": "Missing status parameter"}, 400
    if status not in ["on", "off"]:
        return {"status": "error", "message": "Invalid status"}, 400
    device = next((d for d in devices if d.device_id == device_id), None)
    if not device:
        return {"status": "error", "message": f"Device {device_id} not found"}, 404

    result = device.set_status(status, chat_id, platform)
    return result, 200 if result.get("status") == "success" else 500

# Main entry point
if __name__ == "__main__":
    # Start MQTT client threads for all devices
    for device in devices:
        mqtt_thread = threading.Thread(target=device.start_mqtt)
        mqtt_thread.daemon = True  # Set as daemon thread
        mqtt_thread.start()

    # Start Flask service
    logger.info(f"Starting Flask API for IoTDevices on port {config.IOT_DEVICE_API_PORT}")
    app.run(host="0.0.0.0", port=config.IOT_DEVICE_API_PORT, threaded=True)