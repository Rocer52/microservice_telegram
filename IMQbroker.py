import pika
import json
import requests
import logging
import config

# 設置日誌，記錄程式執行資訊
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 全局標誌，避免重複消費 IMQueue
_is_consuming = False

# 函數：調用 IMLine.py 或 IMTelegram.py 的 API 發送消息
def send_message(chat_id: str, text: str, platform: str = "telegram") -> bool:
    if platform == "telegram":
        url = f"http://localhost:5000/SendMsg"  # Telegram 的 API 路由
    else:  # platform == "line"
        url = f"http://localhost:5001/SendMsg"  # LINE 的 API 路由

    # 根據平台設置參數名稱（Telegram 用 chat_id，LINE 用 user_id）
    params = {"chat_id": chat_id, "message": text} if platform == "telegram" else {"user_id": chat_id, "message": text}
    try:
        response = requests.get(url, params=params)  # 發送 HTTP GET 請求
        if response.status_code == 200 and response.json().get("ok"):
            logger.info(f"通過 API 成功發送消息: platform={platform}, chat_id={chat_id}, text={text}")
            return True
        else:
            logger.error(f"通過 API 發送消息失敗: {response.text}")
            return False
    except Exception as e:
        logger.error(f"通過 API 發送消息時發生錯誤: {e}")
        return False

# 函數：消費 IMQueue 中的消息，並處理設備狀態
def consume_im_queue():
    global _is_consuming
    if _is_consuming:
        logger.info("IMQbroker 已在消費中，跳過重複啟動")
        return
    _is_consuming = True  # 設置消費標誌
    try:
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(host=config.RABBITMQ_HOST, port=config.RABBITMQ_PORT)
        )  # 連接到 RabbitMQ
        channel = connection.channel()  # 創建通道
        channel.queue_declare(queue=config.RABBITMQ_QUEUE)  # 聲明隊列

        # 回調函數：處理接收到的消息
        def callback(ch, method, properties, body):
            message = json.loads(body)  # 解析消息
            logger.info(f"從 IM Queue 收到消息: {message}")

            # 只處理設備狀態消息
            if "device_status" in message:
                chat_id = message.get("chat_id")  # 獲取聊天 ID
                status = message["device_status"]  # 獲取設備狀態
                platform = message.get("platform", "telegram")  # 獲取平台
                if chat_id:
                    if status == "enabled":
                        send_message(chat_id, "設備已啟用", platform)  # 設備啟用時回覆
                    elif status == "disabled":
                        send_message(chat_id, "設備已禁用", platform)  # 設備禁用時回覆
                    else:
                        send_message(chat_id, f"設備狀態: {status}", platform)  # 其他狀態回覆

        channel.basic_consume(queue=config.RABBITMQ_QUEUE, on_message_callback=callback, auto_ack=True)  # 開始消費隊列
        logger.info("開始消費 IM Queue...")
        channel.start_consuming()  # 進入消費循環
    except Exception as e:
        logger.error(f"消費 IM Queue 時發生錯誤: {e}")
        _is_consuming = False  # 重置消費標誌
        raise e