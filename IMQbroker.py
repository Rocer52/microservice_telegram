# IMQbroker.py
import pika
import json
import requests
import logging
import config
import time
from IoTQbroker import bindings, Device

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

greeted_users = set()

def send_message(chat_id: str, text: str, platform: str = "telegram", user_id: str = None, username: str = None) -> bool:
    """Send message to the appropriate platform based on the platform parameter"""
    try:
        if platform == "telegram":
            url = f"http://{config.TELEGRAM_API_HOST}:{config.TELEGRAM_API_PORT}/SendMsg"
            params = {
                "chat_id": chat_id,
                "message": text,
                "user_id": user_id,
                "bot_token": config.TELEGRAM_BOT_TOKEN
            }
        elif platform == "line":
            url = f"http://{config.LINE_API_HOST}:{config.LINE_API_PORT}/SendMsg"
            params = {
                "user_id": chat_id,  # LINE uses user_id instead of chat_id
                "message": text,
                "caller_user_id": username,
                "bot_token": config.LINE_ACCESS_TOKEN
            }
        else:
            logger.error(f"Unsupported platform: {platform}")
            return False

        logger.info(f"Sending message to {platform} API: {url}")
        response = requests.get(url, params=params, timeout=5)
        
        if response.status_code == 200 and response.json().get("ok"):
            logger.info(f"Message sent successfully to {platform}: {text}")
            return True
        else:
            logger.error(f"Failed to send message to {platform}: {response.text}")
            return False
    except Exception as e:
        logger.error(f"Error sending message to {platform}: {e}")
        return False

def consume_queue(queue_name: str):
    connection = None
    channel = None

    def init_rabbitmq():
        nonlocal connection, channel
        try:
            parameters = pika.ConnectionParameters(
                host=config.RABBITMQ_HOST, 
                port=config.RABBITMQ_PORT, 
                heartbeat=30, 
                blocked_connection_timeout=60
            )
            connection = pika.BlockingConnection(parameters)
            channel = connection.channel()
            logger.info(f"Initialized RabbitMQ connection: host={config.RABBITMQ_HOST}, port={config.RABBITMQ_PORT}")
        except Exception as e:
            logger.error(f"Failed to initialize RabbitMQ connection: {e}")
            time.sleep(5)
            init_rabbitmq()

    if not connection or connection.is_closed:
        init_rabbitmq()

    try:
        # 声明队列
        channel.queue_declare(queue=queue_name, durable=True)
        channel.basic_qos(prefetch_count=1)

        def callback(ch, method, properties, body):
            try:
                message = json.loads(body)
                logger.info(f"Received message from queue {queue_name}: {message}")

                # 从消息中获取平台信息
                platform = message.get("platform", "unknown")
                chat_id = message.get("chat_id")
                device_status = message.get("device_status")
                device_id = message.get("device_id", config.DEVICE_ID)
                user_id = message.get("user_id")
                username = message.get("username", "User")
                bot_token = message.get("bot_token")
                
                if not device_status:
                    logger.warning(f"Message does not contain device_status: {message}")
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                    return
                
                if not chat_id:
                    logger.error(f"No chat_id found in message: {message}")
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                    return

                # 验证平台
                if platform not in ["telegram", "line"]:
                    logger.error(f"Invalid platform: {platform}")
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                    return

                # 通知发起用户
                greeting = f"Hi, {username}\n" if chat_id not in greeted_users else ""
                if greeting:
                    greeted_users.add(chat_id)
                formatted_message = f"{greeting}Device {device_id} is now {device_status}, operated by user {username}"
                
                # 使用平台特定的send_message函数
                success = send_message(chat_id, formatted_message, platform, user_id=user_id, username=username)
                if not success:
                    logger.warning(f"Failed to send status update to chat_id={chat_id} on platform {platform}")

                # 通知所有绑定用户，排除发起用户和已通知的群组成员
                notified_users = {chat_id}
                device = Device("LivingRoomLight", device_id=device_id)
                
                if device.group_id and device.group_members:
                    notified_users.update(device.group_members)
                    for member in device.group_members:
                        if member != chat_id:
                            other_message = f"Device {device_id} has been set to {device_status} by user {username}"
                            send_message(member, other_message, platform, user_id=user_id, username=username)

                # 获取设备的所有绑定用户
                bound_users = bindings.get(device_id, set())
                for bound_user in bound_users:
                    if bound_user not in notified_users:
                        other_message = f"Device {device_id} has been set to {device_status} by user {username}"
                        send_message(bound_user, other_message, platform, user_id=user_id, username=username)
                        notified_users.add(bound_user)

                ch.basic_ack(delivery_tag=method.delivery_tag)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse message body as JSON: {e}, body={body}")
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            except Exception as e:
                logger.error(f"Error processing message: {e}, body={body}", exc_info=True)
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

        channel.basic_consume(queue=queue_name, on_message_callback=callback, auto_ack=False)
        logger.info(f"Started consuming {queue_name}...")
        channel.start_consuming()
    except Exception as e:
        logger.error(f"Error consuming queue {queue_name}: {e}")
        if connection and not connection.is_closed:
            connection.close()
        time.sleep(5)
        consume_queue(queue_name)

# LINE专用队列消费者
def consume_line_queue():
    consume_queue(config.RABBITMQ_LINE_QUEUE)

# Telegram专用队列消费者
def consume_telegram_queue():
    consume_queue(config.RABBITMQ_TELEGRAM_QUEUE)