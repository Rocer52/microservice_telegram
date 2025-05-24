import paho.mqtt.client as mqtt
import pika
import json
import config
import logging
import threading
import time
import requests
from flask import Flask, request, send_from_directory
from flask_swagger_ui import get_swaggerui_blueprint
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__)

# Store processed message IDs to prevent duplicate processing
processed_messages = set()

class RaspberryPiDevice:
    def __init__(self, name: str, device_id: str, broker_host: str = config.IOTQUEUE_HOST, broker_port: int = config.IOTQUEUE_PORT):
        self.name = name
        self.device_id = device_id
        self.manufacturer = "raspberrypi"
        self.device_type = "light" if "light" in device_id else "fan"
        self.broker_host = broker_host
        self.broker_port = broker_port
        try:
            self.client = mqtt.Client(client_id=f"pi_device_{name}_{device_id}")
            self.client.on_connect = self.on_connect
            self.client.on_message = self.on_message
            self.client.on_disconnect = self.on_disconnect
            self.client.reconnect_delay_set(min_delay=1, max_delay=120)
        except Exception as e:
            logger.error(f"Failed to initialize MQTT client for Raspberry Pi {name} ({device_id}): {e}")
            raise

        # Initialize RabbitMQ connection
        self.rabbitmq_connection = None
        self.rabbitmq_channel = None
        self.connect_rabbitmq()

    def connect_rabbitmq(self):
        max_retries = 5
        retry_count = 0
        while retry_count < max_retries:
            try:
                parameters = pika.ConnectionParameters(
                    host=config.RABBITMQ_HOST,
                    port=config.RABBITMQ_PORT,
                    heartbeat=30,
                    blocked_connection_timeout=60
                )
                self.rabbitmq_connection = pika.BlockingConnection(parameters)
                self.rabbitmq_channel = self.rabbitmq_connection.channel()
                self.rabbitmq_channel.queue_declare(queue=config.RABBITMQ_QUEUE, durable=True)
                logger.info(f"Connected to RabbitMQ: host={config.RABBITMQ_HOST}, port={config.RABBITMQ_PORT}")
                return
            except Exception as e:
                retry_count += 1
                logger.error(f"Failed to connect to RabbitMQ (attempt {retry_count}/{max_retries}): {e}")
                if retry_count == max_retries:
                    logger.error(f"Max retries reached for RabbitMQ connection. Giving up.")
                    raise
                time.sleep(5)

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info(f"Raspberry Pi {self.name} ({self.device_id}) connected to IOTQueue broker")
            # Subscribe to new topics
            self.client.subscribe(f"{self.manufacturer}/{self.device_type}/enable")
            self.client.subscribe(f"{self.manufacturer}/{self.device_type}/disable")
            self.client.subscribe(f"{self.manufacturer}/{self.device_type}/get_status")
            logger.info(f"Subscribed to topics: {self.manufacturer}/{self.device_type}/enable, {self.manufacturer}/{self.device_type}/disable, {self.manufacturer}/{self.device_type}/get_status")
        else:
            logger.error(f"Raspberry Pi {self.name} ({self.device_id}) connection failed, return code: {rc}")

    def on_disconnect(self, client, userdata, rc):
        logger.warning(f"Raspberry Pi {self.name} ({self.device_id}) disconnected from IOTQueue broker, attempting to reconnect...")

    def on_message(self, client, userdata, msg):
        topic = msg.topic
        payload = msg.payload.decode()
        logger.info(f"Raspberry Pi {self.name} ({self.device_id}) received message - Topic: {topic}, Payload: {payload}")

        try:
            payload_dict = json.loads(payload)
            command = payload_dict.get("command")
            chat_id = payload_dict.get("chat_id")
            platform = payload_dict.get("platform", "telegram")
            device_id = payload_dict.get("device_id", self.device_id)
            # Generate a unique message ID
            message_id = f"{chat_id}_{command}_{device_id}_{time.time()}"
            if message_id in processed_messages:
                logger.info(f"Duplicate message ignored: message_id={message_id}")
                return
            processed_messages.add(message_id)
            # Clean up old message IDs to prevent memory growth
            if len(processed_messages) > 1000:
                processed_messages.clear()

            if device_id != self.device_id:
                logger.info(f"Ignoring message for device_id {device_id}, this device is {self.device_id}")
                return
            logger.info(f"Processing command: {command} for device_id: {device_id}")
            if topic.endswith("/enable") and command == "on":
                self.enable(chat_id, platform)
            elif topic.endswith("/disable") and command == "off":
                self.disable(chat_id, platform)
            elif topic.endswith("/get_status") and command == "get_status":
                self.get_status(chat_id, platform)
            else:
                logger.warning(f"Unknown command or topic: {command} on {topic}")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse payload as JSON: {e}, payload: {payload}")
        except Exception as e:
            logger.error(f"Error processing message for Raspberry Pi {self.device_id}: {e}, payload: {payload}", exc_info=True)

    def notify_status(self, status: str, chat_id: str = None, platform: str = "telegram"):
        from IoTQbroker import bindings
        message = {"device_status": status, "chat_id": chat_id, "platform": platform, "device_id": self.device_id}
        try:
            self.rabbitmq_channel.exchange_declare(exchange="im_exchange", exchange_type="topic")  # 統一為 topic
            # Use new IM topic structure: platform/chat_id/eventcommand
            routing_key = f"{platform}/{chat_id}/status_update" if chat_id else platform
            self.rabbitmq_channel.basic_publish(
                exchange="im_exchange",
                routing_key=routing_key,
                body=json.dumps(message),
                properties=pika.BasicProperties(delivery_mode=2)
            )
            logger.info(f"Sent device status to IM Queue: routing_key={routing_key}, message={message}")
            self.client.publish(f"{self.manufacturer}/{self.device_type}/status", json.dumps({"status": status}))
        except (pika.exceptions.StreamLostError, pika.exceptions.ConnectionClosed) as e:
            logger.warning(f"RabbitMQ connection lost: {e}, attempting to reconnect...")
            self.connect_rabbitmq()
            try:
                self.rabbitmq_channel.exchange_declare(exchange="im_exchange", exchange_type="topic")  # 統一為 topic
                routing_key = f"{platform}/{chat_id}/status_update" if chat_id else platform
                self.rabbitmq_channel.basic_publish(
                    exchange="im_exchange",
                    routing_key=routing_key,
                    body=json.dumps(message),
                    properties=pika.BasicProperties(delivery_mode=2)
                )
                logger.info(f"Sent device status to IM Queue after reconnect: routing_key={routing_key}, message={message}")
                self.client.publish(f"{self.manufacturer}/{self.device_type}/status", json.dumps({"status": status}))
            except Exception as e:
                logger.error(f"Failed to send status to IM Queue after reconnect: {e}")
        except Exception as e:
            logger.error(f"Failed to send device status to IM Queue: {e}, message={message}")

    def get_api_base_url(self):
        return f"http://{config.RASPBERRY_PI_DEVICE_HOST}:{config.RASPBERRY_PI_DEVICE_PORT}"

    def enable(self, chat_id: str = None, platform: str = "telegram"):
        try:
            response = requests.get(f"{self.get_api_base_url()}/Enable", params={"device_id": self.device_id}, timeout=5)
            if response.status_code == 200 and response.json().get("status") == "success":
                logger.info(f"Raspberry Pi {self.name} ({self.device_id}) enabled")
                self.notify_status("enabled", chat_id, platform)
                return response.json()
            else:
                logger.error(f"Failed to enable device: {response.text}")
                return {"status": "error", "message": "Failed to enable device"}
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to call Raspberry Pi Enable API: {e}")
            return {"status": "error", "message": "Failed to call API"}
        except Exception as e:
            logger.error(f"Unexpected error in enable for Raspberry Pi {self.device_id}: {e}")
            return {"status": "error", "message": "Unexpected error"}

    def disable(self, chat_id: str = None, platform: str = "telegram"):
        try:
            response = requests.get(f"{self.get_api_base_url()}/Disable", params={"device_id": self.device_id}, timeout=5)
            if response.status_code == 200 and response.json().get("status") == "success":
                logger.info(f"Raspberry Pi {self.name} ({self.device_id}) disabled")
                self.notify_status("disabled", chat_id, platform)
                return response.json()
            else:
                logger.error(f"Failed to disable device: {response.text}")
                return {"status": "error", "message": "Failed to disable device"}
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to call Raspberry Pi Disable API: {e}")
            return {"status": "error", "message": "Failed to call API"}
        except Exception as e:
            logger.error(f"Unexpected error in disable for Raspberry Pi {self.device_id}: {e}")
            return {"status": "error", "message": "Unexpected error"}

    def get_status(self, chat_id: str = None, platform: str = "telegram"):
        try:
            response = requests.get(f"{self.get_api_base_url()}/GetStatus", params={"device_id": self.device_id}, timeout=5)
            if response.status_code == 200 and response.json().get("state") is not None:
                state = response.json().get("state")
                logger.info(f"Raspberry Pi {self.name} ({self.device_id}) status: {state}")
                self.notify_status(state, chat_id, platform)
                return response.json()
            else:
                logger.error(f"Failed to get device status: {response.text}")
                return {"status": "error", "message": "Failed to get device status"}
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to call Raspberry Pi GetStatus API: {e}")
            return {"status": "error", "message": "Failed to call API"}
        except Exception as e:
            logger.error(f"Unexpected error in get_status for Raspberry Pi {self.device_id}: {e}")
            return {"status": "error", "message": "Unexpected error"}

    def start_mqtt(self):
        max_retries = 5
        retry_count = 0
        while retry_count < max_retries:
            try:
                self.client.connect(self.broker_host, self.broker_port)
                self.client.loop_start()
                logger.info(f"Raspberry Pi {self.name} ({self.device_id}) MQTT started, waiting for messages...")
                return
            except Exception as e:
                retry_count += 1
                logger.error(f"Raspberry Pi {self.name} ({self.device_id}) MQTT start failed (attempt {retry_count}/{max_retries}): {e}")
                if retry_count == max_retries:
                    logger.error(f"Max retries reached for MQTT connection. Giving up.")
                    raise
                time.sleep(5)

    def stop(self):
        try:
            self.client.loop_stop()
            self.client.disconnect()
            if self.rabbitmq_connection and not self.rabbitmq_connection.is_closed:
                self.rabbitmq_connection.close()
            logger.info(f"Raspberry Pi {self.name} ({self.device_id}) stopped")
        except Exception as e:
            logger.error(f"Failed to stop Raspberry Pi {self.device_id}: {e}")

# Create Raspberry Pi device instance
pi_device = RaspberryPiDevice("LivingRoomLight", device_id="raspberrypi_light_001")

# Flask Routes
@app.route('/Enable', methods=['GET'])
def enable_route():
    device_id = request.args.get('device_id')
    chat_id = request.args.get('chat_id')
    platform = request.args.get('platform', 'telegram')
    try:
        if not device_id:
            return {"status": "error", "message": "Missing device_id parameter"}, 400
        if device_id != pi_device.device_id:
            return {"status": "error", "message": f"Device {device_id} not found"}, 404
        result = pi_device.enable(chat_id, platform)
        return result, 200 if result.get("status") == "success" else 500
    except Exception as e:
        logger.error(f"Error in enable_route for device_id={device_id}: {e}", exc_info=True)
        return {"status": "error", "message": "Internal server error"}, 500

@app.route('/Disable', methods=['GET'])
def disable_route():
    device_id = request.args.get('device_id')
    chat_id = request.args.get('chat_id')
    platform = request.args.get('platform', 'telegram')
    try:
        if not device_id:
            return {"status": "error", "message": "Missing device_id parameter"}, 400
        if device_id != pi_device.device_id:
            return {"status": "error", "message": f"Device {device_id} not found"}, 404
        result = pi_device.disable(chat_id, platform)
        return result, 200 if result.get("status") == "success" else 500
    except Exception as e:
        logger.error(f"Error in disable_route for device_id={device_id}: {e}", exc_info=True)
        return {"status": "error", "message": "Internal server error"}, 500

@app.route('/GetStatus', methods=['GET'])
def get_status_route():
    device_id = request.args.get('device_id')
    chat_id = request.args.get('chat_id')
    platform = request.args.get('platform', 'telegram')
    try:
        if not device_id:
            return {"status": "error", "message": "Missing device_id parameter"}, 400
        if device_id != pi_device.device_id:
            return {"status": "error", "message": f"Device {device_id} not found"}, 404
        result = pi_device.get_status(chat_id, platform)
        return result, 200 if result.get("status") == "success" else 500
    except Exception as e:
        logger.error(f"Error in get_status_route for device_id={device_id}: {e}", exc_info=True)
        return {"status": "error", "message": "Internal server error"}, 500

# Swagger UI setup
SWAGGER_URL = '/swagger'
API_URL = '/static/openapi.yaml'
swaggerui_blueprint = get_swaggerui_blueprint(
    SWAGGER_URL,
    API_URL,
    config={
        'app_name': "IM and IoT Microservices"
    }
)
app.register_blueprint(swaggerui_blueprint, url_prefix=SWAGGER_URL)

# Serve the openapi.yaml file
@app.route('/static/<path:path>')
def send_swagger(path):
    return send_from_directory('static', path)

if __name__ == "__main__":
    # Ensure the static directory and openapi.yaml exist
    if not os.path.exists('static'):
        os.makedirs('static')
    with open('static/openapi.yaml', 'w') as f:
        with open('openapi.yaml', 'r') as src:
            f.write(src.read())

    try:
        mqtt_thread = threading.Thread(target=pi_device.start_mqtt)
        mqtt_thread.daemon = True
        mqtt_thread.start()
        logger.info(f"Starting Flask API for RaspberryPiDevice on port {config.RASPBERRY_PI_DEVICE_PORT}")
        app.run(host="0.0.0.0", port=config.RASPBERRY_PI_DEVICE_PORT, threaded=True, debug=False)
    except Exception as e:
        logger.error(f"Failed to start RaspberryPiDevice service: {e}", exc_info=True)
        raise