import paho.mqtt.client as mqtt
import time

class VirtualDevice:
    def __init__(self, name: str, broker_host: str = "localhost", broker_port: int = 1883):
        self.name = name
        self.state = "off"
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.client = mqtt.Client(client_id=f"virtual_device_{name}")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            print(f"{self.name} 已連接到 MQTT broker")
            self.client.subscribe("light/command")
        else:
            print(f"{self.name} 連線失敗，錯誤碼: {rc}")
            exit(1)

    def on_message(self, client, userdata, msg):
        topic = msg.topic
        payload = msg.payload.decode()
        print(f"{self.name} 收到訊息 - 主題: {topic}, 內容: {payload}")

        if topic == "light/command":
            if payload == "on":
                self.enable()
            elif payload == "off":
                self.disable()

    def enable(self):
        self.state = "on"
        print(f"{self.name} 已開啟")
        self.client.publish("light/status", "on")

    def disable(self):
        self.state = "off"
        print(f"{self.name} 已關閉")
        self.client.publish("light/status", "off")

    def start(self):
        try:
            self.client.connect(self.broker_host, self.broker_port, 60)
            self.client.loop_start()
            print(f"{self.name} 已啟動，等待訊息...")
        except Exception as e:
            print(f"{self.name} 啟動失敗: {e}")
            exit(1)

    def stop(self):
        self.client.loop_stop()
        self.client.disconnect()
        print(f"{self.name} 已停止")

if __name__ == "__main__":
    device = VirtualDevice("LivingRoomLight")
    device.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        device.stop()