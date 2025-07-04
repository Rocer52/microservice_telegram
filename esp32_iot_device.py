# esp32_iot_device.py
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
try:
    from Crypto.Signature import DSS
    from Crypto.Hash import SHA256
    from Crypto.PublicKey import ECC
except ImportError:
    DSS = None
    SHA256 = None
    ECC = None
    logging.warning("pycryptodome not installed, signature generation disabled")
import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('esp32.log', mode='a')
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
private_key = None

def load_private_key():
    global private_key
    if ECC is None:
        logger.error("pycryptodome not available, cannot load private key")
        return False
    try:
        if not os.path.exists("ecdsa_private.pem"):
            logger.error("Private key file 'ecdsa_private.pem' not found")
            return False
        with open("ecdsa_private.pem", "rt") as f:
            private_key = ECC.import_key(f.read())
        logger.info("Private key loaded successfully")
        return True
    except Exception as e:
        logger.error(f"Failed to load private key: {e}")
        return False

def generate_signature(chat_id: str):
    if not private_key or DSS is None or SHA256 is None:
        logger.error("No private key or pycryptodome not available")
        return {"success": False, "error": "No private key or pycryptodome not available"}
    
    try:
        timestamp = str(int(time.time()))
        message = f"{chat_id}:{timestamp}".encode('utf-8')
        h = SHA256.new(message)
        signer = DSS.new(private_key, 'fips-186-3')
        signature = signer.sign(h)
        signature_b64 = base64.b64encode(signature).decode('utf-8')
        result = {
            "success": True,
            "chat_id": chat_id,
            "timestamp": timestamp,
            "signature": signature_b64,
            "username": "",
            "bot_token": ""
        }
        logger.info(f"Generated signature for chat_id: {chat_id}")
        return result
    except Exception as e:
        logger.error(f"Error generating signature: {e}")
        return {"success": False, "error": str(e)}

class ESP32Device:
    def __init__(self, name: str, device_id: str):
        self.name = name
        self.device_id = device_id
        self.manufacturer = "esp32"
        self.device_type = "light" if "light" in device_id else "fan"
        self.mqtt_client = self.setup_mqtt()
        self.rabbitmq_connection = None
        self.rabbitmq_channel = None
        self.rabbitmq_ioloop_thread = None
        self.setup_rabbitmq_async()

    def setup_mqtt(self):
        client = mqtt.Client(client_id=f"esp32_{self.device_id}")
        client.on_connect = self.on_mqtt_connect
        client.on_message = self.on_mqtt_message
        client.on_disconnect = self.on_mqtt_disconnect
        client.reconnect_delay_set(min_delay=1, max_delay=120)
        return client

    def setup_rabbitmq_async(self):
        try:
            parameters = pika.ConnectionParameters(
                host=config.RABBITMQ_HOST,
                port=config.RABBITMQ_PORT
            )
            self.rabbitmq_connection = pika.SelectConnection(
                parameters,
                on_open_callback=self.on_rabbitmq_open,
                on_open_error_callback=self.on_rabbitmq_open_error,
                on_close_callback=self.on_rabbitmq_close
            )
            self.rabbitmq_ioloop_thread = threading.Thread(target=self.rabbitmq_connection.ioloop.start)
            self.rabbitmq_ioloop_thread.daemon = True
            self.rabbitmq_ioloop_thread.start()
            logger.info("RabbitMQ async connection initiated")
        except Exception as e:
            logger.error(f"Failed to initiate RabbitMQ async connection: {e}")

    def on_rabbitmq_open(self, connection):
        self.rabbitmq_channel = connection.channel(on_open_callback=self.on_channel_open)

    def on_channel_open(self, channel):
        self.rabbitmq_channel = channel
        logger.info("RabbitMQ channel opened")

    def on_rabbitmq_open_error(self, connection, error):
        logger.error(f"RabbitMQ connection failed: {error}")
        time.sleep(5)
        self.setup_rabbitmq_async()

    def on_rabbitmq_close(self, connection, reason):
        logger.warning(f"RabbitMQ connection closed: {reason}")
        self.rabbitmq_channel = None
        self.rabbitmq_connection = None
        time.sleep(5)
        self.setup_rabbitmq_async()

    # 添加缺失的 MQTT 回调方法
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
            payload = json.loads(msg.payload.decode('utf-8'))
            logger.info(f"Received MQTT message: {msg.topic}")
            
            if msg.topic.endswith("enable"):
                self.handle_enable(payload)
            elif msg.topic.endswith("disable"):
                self.handle_disable(payload)
            elif msg.topic.endswith("get_status"):
                self.handle_get_status(payload)
                
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in MQTT message: {e}")
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
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if not self.rabbitmq_channel or self.rabbitmq_channel.is_closed:
                    logger.info(f"RabbitMQ channel closed, reconnecting (attempt {attempt + 1}/{max_retries})...")
                    self.setup_rabbitmq_async()
                    time.sleep(2)
                
                # 根据平台选择不同队列
                if platform == "line":
                    queue_name = config.RABBITMQ_LINE_QUEUE
                elif platform == "telegram":
                    queue_name = config.RABBITMQ_TELEGRAM_QUEUE
                else:
                    logger.error(f"Unsupported platform: {platform}")
                    return
                
                # 声明队列并发布消息
                self.rabbitmq_channel.queue_declare(queue=queue_name, durable=True)
                self.rabbitmq_channel.basic_publish(
                    exchange='',  # 直接发送到队列
                    routing_key=queue_name,
                    body=json.dumps(message),
                    properties=pika.BasicProperties(
                        delivery_mode=2,  # 使消息持久化
                    )
                )
                logger.info(f"Status update sent to {queue_name} for device_id: {self.device_id}, status: {status}")
                break
            except (pika.exceptions.StreamLostError, pika.exceptions.ConnectionClosed, 
                    pika.exceptions.ChannelClosed, pika.exceptions.ChannelClosedByBroker, 
                    ConnectionResetError, requests.RequestException) as e:
                logger.error(f"Failed to send status update (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(2)
                else:
                    logger.error("Max retries reached, giving up")

    def handle_enable(self, payload):
        chat_id = payload.get("chat_id")
        platform = payload.get("platform", "telegram")
        username = payload.get("username", "User")
        bot_token = payload.get("bot_token", "")
        
        try:
            signature = generate_signature(chat_id)
            if not signature["success"]:
                logger.error(f"Signature generation failed: {signature['error']}")
                return
            
            device_config = config.load_device_config()
            url = f"http://{device_config['esp32']['host']}:{device_config['esp32']['port']}/Enable"
            
            data = {
                "device_id": self.device_id,
                "chat_id": chat_id,
                "timestamp": signature["timestamp"],
                "signature": signature["signature"],
                "username": username,
                "bot_token": bot_token
            }
            
            logger.info(f"Sending enable request to {url}")
            response = requests.post(
                url,
                json=data,
                timeout=5
            )
            
            if response.status_code == 200:
                logger.info(f"Enable request succeeded for device_id: {self.device_id}")
                self.notify_status("on", chat_id, platform, username, bot_token)
            else:
                logger.error(f"Enable request failed: {response.status_code} - {response.text}")
        except requests.RequestException as e:
            logger.error(f"Error calling enable API: {e}")

    def handle_disable(self, payload):
        chat_id = payload.get("chat_id")
        platform = payload.get("platform", "telegram")
        username = payload.get("username", "User")
        bot_token = payload.get("bot_token", "")
        
        try:
            signature = generate_signature(chat_id)
            if not signature["success"]:
                logger.error(f"Signature generation failed: {signature['error']}")
                return
            
            device_config = config.load_device_config()
            url = f"http://{device_config['esp32']['host']}:{device_config['esp32']['port']}/Disable"
            
            data = {
                "device_id": self.device_id,
                "chat_id": chat_id,
                "timestamp": signature["timestamp"],
                "signature": signature["signature"],
                "username": username,
                "bot_token": bot_token
            }
            
            logger.info(f"Sending disable request to {url}")
            response = requests.post(
                url,
                json=data,
                timeout=5
            )
            
            if response.status_code == 200:
                logger.info(f"Disable request succeeded for device_id: {self.device_id}")
                self.notify_status("off", chat_id, platform, username, bot_token)
            else:
                logger.error(f"Disable request failed: {response.status_code} - {response.text}")
        except requests.RequestException as e:
            logger.error(f"Error calling disable API: {e}")

    def handle_get_status(self, payload):
        chat_id = payload.get("chat_id")
        platform = payload.get("platform", "telegram")
        username = payload.get("username", "User")
        bot_token = payload.get("bot_token", "")
        
        try:
            signature = generate_signature(chat_id)
            if not signature["success"]:
                logger.error(f"Signature generation failed: {signature['error']}")
                return
            
            device_config = config.load_device_config()
            url = f"http://{device_config['esp32']['host']}:{device_config['esp32']['port']}/GetStatus"
            
            data = {
                "device_id": self.device_id,
                "chat_id": chat_id,
                "timestamp": signature["timestamp"],
                "signature": signature["signature"],
                "username": username,
                "bot_token": bot_token
            }
            
            logger.info(f"Sending get_status request to {url}")
            response = requests.post(
                url,
                json=data,
                timeout=5
            )
            
            if response.status_code == 200:
                status = response.json().get("state", "unknown")
                logger.info(f"GetStatus request succeeded: {status}")
                self.notify_status(status, chat_id, platform, username, bot_token)
            else:
                logger.error(f"GetStatus request failed: {response.status_code} - {response.text}")
        except requests.RequestException as e:
            logger.error(f"Error calling GetStatus API: {e}")

    def start_mqtt(self):
        try:
            self.mqtt_client.connect(config.IOTQUEUE_HOST, config.IOTQUEUE_PORT)
            self.mqtt_client.loop_start()
            logger.info(f"MQTT started for {self.device_id}")
        except Exception as e:
            logger.error(f"Failed to start MQTT: {e}")

    def stop(self):
        try:
            if self.rabbitmq_connection and not self.rabbitmq_connection.is_closed:
                self.rabbitmq_connection.ioloop.add_callback_threadsafe(self.rabbitmq_connection.close)
                self.rabbitmq_connection.ioloop.stop()
            if self.rabbitmq_ioloop_thread and self.rabbitmq_ioloop_thread.is_alive():
                self.rabbitmq_ioloop_thread.join(timeout=5)
            logger.info("RabbitMQ connection closed")
        except Exception as e:
            logger.error(f"Error stopping RabbitMQ: {e}")

esp32_device = ESP32Device("LivingRoomLight", config.DEVICE_ID)

@app.route('/Enable', methods=['GET'])
def api_enable():
    device_id = request.args.get('device_id')
    if not device_id or device_id != esp32_device.device_id:
        logger.error(f"Invalid device ID: {device_id}")
        return jsonify({"status": "error", "message": "Invalid device ID"}), 400
    
    try:
        device_config = config.load_device_config()
        url = f"http://{device_config['esp32']['host']}:{device_config['esp32']['port']}/Enable"
        logger.info(f"Sending enable API request to {url}")
        response = requests.get(
            url,
            params={"device_id": device_id},
            timeout=5
        )
        return jsonify(response.json()), response.status_code
    except requests.RequestException as e:
        logger.error(f"Error in enable API: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/Disable', methods=['GET'])
def api_disable():
    device_id = request.args.get('device_id')
    if not device_id or device_id != esp32_device.device_id:
        logger.error(f"Invalid device ID: {device_id}")
        return jsonify({"status": "error", "message": "Invalid device ID"}), 400
    
    try:
        device_config = config.load_device_config()
        url = f"http://{device_config['esp32']['host']}:{device_config['esp32']['port']}/Disable"
        logger.info(f"Sending disable API request to {url}")
        response = requests.get(
            url,
            params={"device_id": device_id},
            timeout=5
        )
        return jsonify(response.json()), response.status_code
    except requests.RequestException as e:
        logger.error(f"Error in disable API: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/GetStatus', methods=['GET'])
def api_get_status():
    device_id = request.args.get('device_id')
    if not device_id or device_id != esp32_device.device_id:
        logger.error(f"Invalid device ID: {device_id}")
        return jsonify({"status": "error", "message": "Invalid device ID"}), 400
    
    try:
        device_config = config.load_device_config()
        url = f"http://{device_config['esp32']['host']}:{device_config['esp32']['port']}/GetStatus"
        logger.info(f"Sending get_status API request to {url}")
        response = requests.get(
            url,
            params={"device_id": device_id},
            timeout=5
        )
        return jsonify(response.json()), response.status_code
    except requests.RequestException as e:
        logger.error(f"Error in get_status API: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/signature', methods=['POST'])
def api_signature():
    data = request.get_json()
    logger.info(f"Received signature request")
    return jsonify({"status": "received"}), 200

SWAGGER_URL = '/swagger'
API_URL = '/static/openapi.yaml'
swaggerui_blueprint = get_swaggerui_blueprint(
    SWAGGER_URL,
    API_URL,
    config={'app_name': "ESP32 IoT Device"}
)
app.register_blueprint(swaggerui_blueprint, url_prefix=SWAGGER_URL)

@app.route('/static/<path:path>')
def serve_swagger(path):
    return send_from_directory('static', path)

if __name__ == "__main__":
    try:
        load_private_key()
        if not os.path.exists('static'):
            os.makedirs('static')
        mqtt_thread = threading.Thread(target=esp32_device.start_mqtt)
        mqtt_thread.daemon = True
        mqtt_thread.start()
        logger.info(f"Starting Flask app on port {config.ESP32_API_PORT}")
        app.run(host="0.0.0.0", port=config.ESP32_API_PORT, debug=False)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
    finally:
        esp32_device.stop()