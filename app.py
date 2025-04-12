from flask import Flask, request
import paho.mqtt.client as mqtt
import config
from command import Device, MQTTMessageAPI, get_status_api
from message_handler import parse_message, send_message

app = Flask(__name__)

# 初始化 MQTT 客戶端
mqtt_client = mqtt.Client()
try:
    mqtt_client.connect(config.MQTT_BROKER_HOST, config.MQTT_BROKER_PORT, 60)
    mqtt_client.loop_start()
except Exception as e:
    print(f"MQTT 連線失敗: {e}")
    exit(1)

# 橋接模式：創建設備
message_api = MQTTMessageAPI(mqtt_client)
device = Device(message_api)

# 儲存最新的設備狀態
device_status = {"state": "off"}

# 提供查詢當前狀態的方法
def get_current_status():
    return device_status["state"]

# 訂閱設備狀態
def on_status_message(topic, payload):
    global device_status
    device_status["state"] = payload
    print(f"收到設備狀態: {device_status}")

if not get_status_api(device, on_status_message):
    print("無法訂閱設備狀態，程式退出")
    exit(1)

#webhook負責處理路由請求
@app.route('/webhook', methods=['POST'])
def handle_webhook():
    data = request.get_json()
    if 'message' in data:
        message_text = data['message']['text']
        chat_id = data['message']['chat']['id']
        success = parse_message(message_text, device, chat_id, get_current_status) #調用 message_handler的parse_message 函數
        if not success:
            print(f"處理訊息失敗: {message_text}")
    return {"ok": True}, 200

@app.route('/enable', methods=['GET'])
def enable():
    if enable_api(device):
        return "Light enabled", 200
    return "Failed to enable light", 500

@app.route('/disable', methods=['GET'])
def disable():
    if disable_api(device):
        return "Light disabled", 200
    return "Failed to disable light", 500

@app.route('/status', methods=['GET'])
def get_status():
    return {"status": device_status["state"]}, 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)