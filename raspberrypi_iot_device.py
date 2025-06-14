from flask import Flask, request, send_from_directory, jsonify
from flask_swagger_ui import get_swaggerui_blueprint
import paho.mqtt.client as mqtt
import pika
import json
import config
import logging
import threading
import time
import os
import base64
from Crypto.Signature import DSS
from Crypto.Hash import SHA256
from Crypto.PublicKey import ECC
import requests

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ECDSA private key
private_key = None

def load_private_key():
    global private_key
    try:
        if not os.path.exists("ecdsa_private.pem"):
            logger.error("Private key file not found")
            return False
        with open("ecdsa_private.pem", "rt") as f:
            private_key = ECC.import_key(f.read())
        logger.info("Private key loaded successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to load private key: {e}")
        return False

def generate_signature(chat_id: str):
    if not private_key:
        return {"success": False, "error": "No private key"}
    
    try:
        timestamp = str(int(time.time()))
        message = f"{chat_id}:{timestamp}".encode()
        h = SHA256.new(message)
        signer = DSS.new(private_key, 'fips-186-3', encoding='der')
        signature = signer.sign(h)
        return {
            "success": True,
            "chat_id": chat_id,
            "timestamp": timestamp,
            "signature": base64.b64encode(signature).decode()
        }
    except Exception as e:
        logger.error(f"Error generating signature: {e}")
        return {"success": False, "error": str(e)}

class RaspberryPiDevice:
    def __init__(self, name: str, device_id: str):
        self.name = name
        self.device_id = device_id
        self.manufacturer = "raspberrypi"
        self.device_type = "light" if "light" in device_id else "fan"
        self.mqtt_client = self.setup_mqtt()
        self.rabbitmq_connection = None
        self.rabbitmq_channel = None
        self.setup_rabbitmq()

    def setup_mqtt(self):
        client = mqtt.Client(client_id=f"pi_{self.device_id}")
        client.on_connect = self.on_mqtt_connect
        client.on_message = self.on_mqtt_message
        client.on_disconnect = self.on_mqtt_disconnect
        client.reconnect_delay_set(min_delay=1, max_delay=120)
        return client

    def setup_rabbitmq(self):
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
            logger.info("RabbitMQ connection established")
        except Exception as e:
            logger.error(f"Failed to connect to RabbitMQ: {e}")

    def on_mqtt_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logger.info(f"Connected to MQTT broker for {self.device_id}")
            client.subscribe(f"{self.manufacturer}/{self.device_type}/#")
        else:
            logger.error(f"MQTT connection failed with code {rc}")

    def on_mqtt_disconnect(self, client, userdata, rc):
        logger.warning(f"MQTT disconnected, attempting to reconnect...")

    def on_mqtt_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
            logger.info(f"Received MQTT message: {msg.topic} - {payload}")

            if msg.topic.endswith("enable"):
                self.handle_enable(payload)
            elif msg.topic.endswith("disable"):
                self.handle_disable(payload)
            elif msg.topic.endswith("get_status"):
                self.handle_get_status(payload)
                
        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}")

    def notify_status(self, status: str, chat_id: str, platform: str, username: str, bot_token: str):
        message = {
            "device_status": status,
            "device_id": self.device_id,
            "chat_id": chat_id,
            "platform": platform,
            "username": username,
            "bot_token": bot_token
        }
        
        try:
            self.rabbitmq_channel.basic_publish(
                exchange="im_exchange",
                routing_key=f"{platform}/{chat_id}/status_update",
                body=json.dumps(message),
                properties=pika.BasicProperties(delivery_mode=2)
            )
            logger.info(f"Status update sent: {message}")
            
            # Generate and send signature
            signature = generate_signature(chat_id)
            if signature["success"]:
                signature["username"] = username
                signature["bot_token"] = bot_token
                requests.post(
                    f"http://{config.ESP32_DEVICE_HOST}:{config.ESP32_DEVICE_PORT}/signature",
                    json=signature,
                    timeout=3
                )
        except Exception as e:
            logger.error(f"Failed to send status update: {e}")

    def handle_enable(self, payload):
        chat_id = payload.get("chat_id")
        platform = payload.get("platform", "telegram")
        username = payload.get("username", "User")
        bot_token = payload.get("bot_token", "")
        
        try:
            response = requests.get(
                f"http://{config.RASPBERRY_PI_DEVICE_HOST}:{config.RASPBERRY_PI_DEVICE_PORT}/Enable",
                params={"device_id": self.device_id},
                timeout=5
            )
            
            if response.status_code == 200:
                self.notify_status("on", chat_id, platform, username, bot_token)
            else:
                logger.error(f"Enable request failed: {response.text}")
        except Exception as e:
            logger.error(f"Error calling enable API: {e}")

    def handle_disable(self, payload):
        chat_id = payload.get("chat_id")
        platform = payload.get("platform", "telegram")
        username = payload.get("username", "User")
        bot_token = payload.get("bot_token", "")
        
        try:
            response = requests.get(
                f"http://{config.RASPBERRY_PI_DEVICE_HOST}:{config.RASPBERRY_PI_DEVICE_PORT}/Disable",
                params={"device_id": self.device_id},
                timeout=5
            )
            
            if response.status_code == 200:
                self.notify_status("off", chat_id, platform, username, bot_token)
            else:
                logger.error(f"Disable request failed: {response.text}")
        except Exception as e:
            logger.error(f"Error calling disable API: {e}")

    def handle_get_status(self, payload):
        chat_id = payload.get("chat_id")
        platform = payload.get("platform", "telegram")
        username = payload.get("username", "User")
        bot_token = payload.get("bot_token", "")
        
        try:
            response = requests.get(
                f"http://{config.RASPBERRY_PI_DEVICE_HOST}:{config.RASPBERRY_PI_DEVICE_PORT}/GetStatus",
                params={"device_id": self.device_id},
                timeout=5
            )
            
            if response.status_code == 200:
                status = response.json().get("state", "unknown")
                self.notify_status(status, chat_id, platform, username, bot_token)
            else:
                logger.error(f"GetStatus request failed: {response.text}")
        except Exception as e:
            logger.error(f"Error calling GetStatus API: {e}")

    def start_mqtt(self):
        try:
            self.mqtt_client.connect(config.IOTQUEUE_HOST, config.IOTQUEUE_PORT)
            self.mqtt_client.loop_start()
            logger.info(f"MQTT started for {self.device_id}")
        except Exception as e:
            logger.error(f"Failed to start MQTT: {e}")

# Create device instance
pi_device = RaspberryPiDevice("LivingRoomLight", "raspberrypi_light_001")

# Flask routes
@app.route('/Enable', methods=['GET'])
def api_enable():
    device_id = request.args.get('device_id')
    if not device_id or device_id != pi_device.device_id:
        return jsonify({"status": "error", "message": "Invalid device ID"}), 400
    
    try:
        response = requests.get(
            f"http://{config.RASPBERRY_PI_DEVICE_HOST}:{config.RASPBERRY_PI_DEVICE_PORT}/Enable",
            params={"device_id": device_id},
            timeout=5
        )
        return jsonify(response.json()), response.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/Disable', methods=['GET'])
def api_disable():
    device_id = request.args.get('device_id')
    if not device_id or device_id != pi_device.device_id:
        return jsonify({"status": "error", "message": "Invalid device ID"}), 400
    
    try:
        response = requests.get(
            f"http://{config.RASPBERRY_PI_DEVICE_HOST}:{config.RASPBERRY_PI_DEVICE_PORT}/Disable",
            params={"device_id": device_id},
            timeout=5
        )
        return jsonify(response.json()), response.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/GetStatus', methods=['GET'])
def api_get_status():
    device_id = request.args.get('device_id')
    if not device_id or device_id != pi_device.device_id:
        return jsonify({"status": "error", "message": "Invalid device ID"}), 400
    
    try:
        response = requests.get(
            f"http://{config.RASPBERRY_PI_DEVICE_HOST}:{config.RASPBERRY_PI_DEVICE_PORT}/GetStatus",
            params={"device_id": device_id},
            timeout=5
        )
        return jsonify(response.json()), response.status_code
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# Swagger UI setup
SWAGGER_URL = '/swagger'
API_URL = '/static/openapi.yaml'
swaggerui_blueprint = get_swaggerui_blueprint(
    SWAGGER_URL,
    API_URL,
    config={'app_name': "Raspberry Pi IoT Device"}
)
app.register_blueprint(swaggerui_blueprint, url_prefix=SWAGGER_URL)

@app.route('/static/<path:path>')
def serve_swagger(path):
    return send_from_directory('static', path)

if __name__ == "__main__":
    # Load private key
    load_private_key()
    
    # Ensure static directory exists
    if not os.path.exists('static'):
        os.makedirs('static')
    
    # Start MQTT in a separate thread
    mqtt_thread = threading.Thread(target=pi_device.start_mqtt)
    mqtt_thread.daemon = True
    mqtt_thread.start()
    
    # Start Flask app
    app.run(host="0.0.0.0", port=config.RASPBERRY_PI_API_PORT)