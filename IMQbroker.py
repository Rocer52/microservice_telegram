import requests
import config
import pika
import json
import logging
from IoTQbroker import IoTParse_Message

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 驗證 chat_id 是否有效
def authenticate(chat_id: str) -> bool:
    if chat_id is None or chat_id == "":
        logger.error("認證失敗：無效的 chat_id")
        return False
    return True

# 解析即時通訊訊息，根據指令返回對應的操作
def IMParse_Message(message_text: str, chat_id: str, group_id: str = None) -> dict:
    if not authenticate(chat_id):
        return {"success": False, "message": "Authentication failed"}

    message_text = message_text.lower().strip()
    logger.info(f"解析訊息: {message_text}, chat_id: {chat_id}, group_id: {group_id}")

    if message_text == "/sendmsg":
        return {"success": True, "action": "sendMsg", "chat_id": chat_id}
    elif message_text == "/sendgroupmsg":
        if group_id:
            return {"success": True, "action": "sendGroupMsg", "group_id": group_id}
        else:
            return {"success": False, "message": "Group ID required for /sendGroupMsg"}
    elif message_text == "/sendallmsg":
        return {"success": True, "action": "sendAllMsg"}
    else:
        return {"success": False}

# 發送訊息到指定平台（Telegram 或 LINE）
def send_message(chat_id: str, text: str, platform: str = "telegram") -> bool:
    if not chat_id:
        logger.error("無法發送訊息：缺少 chat_id")
        return False
    
    if platform == "telegram":
        url = f"{config.TELEGRAM_API_URL}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
    elif platform == "line":
        url = f"{config.LINE_API_URL}/push"
        headers = {"Authorization": f"Bearer {config.LINE_ACCESS_TOKEN}"}
        payload = {
            "to": chat_id,
            "messages": [{"type": "text", "text": text}]
        }
    else:
        logger.error(f"未知的平台: {platform}")
        return False

    try:
        response = requests.post(url, json=payload, headers=headers if platform == "line" else None)
        if response.status_code == 200:
            logger.info(f"訊息發送成功: platform={platform}, chat_id={chat_id}, text={text}")
            return True
        else:
            logger.error(f"{platform.capitalize()} API 回應錯誤: {response.status_code}, {response.text}")
            return False
    except Exception as e:
        logger.error(f"{platform.capitalize()} 訊息發送失敗: {e}")
        return False

# 發送群組訊息
def send_group_message(group_id: str, text: str, platform: str = "telegram") -> bool:
    return send_message(group_id, text, platform)

# 廣播訊息給所有 chat_ids
def broadcast_message(text: str, chat_ids: list, platform: str = "telegram") -> bool:
    if not chat_ids:
        logger.warning("無法廣播訊息：沒有已知的 chat_ids")
        return False
    success = True
    for chat_id in chat_ids:
        if not send_message(chat_id, text, platform):
            success = False
    return success

# 消費 IMQueue 消息，處理命令和設備狀態更新
def consume_im_queue(channel, chat_ids: set, device, platform: str = "telegram"):
    def callback(ch, method, properties, body):
        try:
            message = json.loads(body)
            logger.info(f"從 IM Queue 收到訊息: {message}")

            # 處理設備狀態更新
            if "device_status" in message:
                chat_id = message.get("chat_id")
                status = message["device_status"]
                platform_in_message = message.get("platform", platform)  # 使用消息中的 platform 或默認值
                if chat_id:
                    if status == "enabled":
                        send_message(chat_id, "設備已開啟", platform_in_message)
                    elif status == "disabled":
                        send_message(chat_id, "設備已關閉", platform_in_message)
                    elif status in ["on", "off"]:
                        send_message(chat_id, f"Device status: {status}", platform_in_message)
                return

            # 處理 IM 或 IoT 命令
            message_text = message.get("message_text", "")
            chat_id = message.get("chat_id")
            group_id = message.get("group_id")
            text = message.get("text")
            platform_in_message = message.get("platform", platform)  # 使用消息中的 platform 或默認值

            if not chat_id and not group_id:
                logger.error("IM Queue 訊息缺少 chat_id 或 group_id")
                return

            # 發送「已接受訊息」（僅對 Webhook 來的消息）
            if "message_text" in message and message_text:
                send_message(chat_id, f"Command received: {message_text}", platform_in_message)

            # 處理 IM 命令
            im_result = IMParse_Message(message_text, chat_id, group_id)
            if im_result["success"]:
                action = im_result["action"]
                if action == "sendMsg":
                    send_message(chat_id, text if text else "This is a message for you!", platform_in_message)
                elif action == "sendGroupMsg":
                    send_group_message(group_id, text if text else "This is a group message!", platform_in_message)
                elif action == "sendAllMsg":
                    broadcast_message(text if text else "This is a broadcast message!", list(chat_ids), platform_in_message)

            # 處理 IoT 命令
            iot_result = IoTParse_Message(message_text, device, chat_id, platform_in_message)
            if not iot_result["success"] and not im_result["success"]:
                send_message(chat_id, "請輸入有效指令", platform_in_message)
        except Exception as e:
            logger.error(f"處理 IM Queue 訊息失敗: {e}")
            if "chat_id" in message:
                send_message(message["chat_id"], f"Error processing message: {str(e)}", platform_in_message)

    channel.basic_consume(
        queue=config.RABBITMQ_QUEUE,
        on_message_callback=callback,
        auto_ack=True
    )
    logger.info("開始消費 IM Queue...")
    channel.start_consuming()

# 啟動 RabbitMQ 消費，供外部調用
def start_consuming(chat_ids: set, device, platform: str = "telegram"):
    try:
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(host=config.RABBITMQ_HOST, port=config.RABBITMQ_PORT)
        )
        channel = connection.channel()
        channel.queue_declare(queue=config.RABBITMQ_QUEUE)
        consume_im_queue(channel, chat_ids, device, platform)
    except Exception as e:
        logger.error(f"RabbitMQ 連線失敗: {e}")
        raise