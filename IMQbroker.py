import pika
import json
import requests
import logging
import config
import time
from IoTQbroker import bindings

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Function: Send IM message to a specific chat_id via IMTelegram/IMLine API
def send_message(chat_id: str, text: str, platform: str = "telegram") -> bool:
    if platform == "telegram":
        url = f"http://{config.TELEGRAM_API_HOST}:{config.TELEGRAM_API_PORT}/SendMsg"
        params = {"chat_id": chat_id, "message": text}
    else:  # line
        url = f"http://{config.LINE_API_HOST}:{config.LINE_API_PORT}/SendMsg"
        params = {"user_id": chat_id, "message": text}

    try:
        response = requests.get(url, params=params, timeout=5)
        if response.status_code == 200 and response.json().get("ok"):
            logger.info(f"Message sent successfully: platform={platform}, chat_id={chat_id}, text={text}")
            return True
        else:
            logger.error(f"Failed to send message: {response.text}")
            return False
    except Exception as e:
        logger.error(f"Error sending message to {chat_id} on {platform}: {e}")
        return False

# Function: Consume messages from all platform-specific IMQueues
def consume_im_queue():
    # Global RabbitMQ connection and channel
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
            channel.exchange_declare(exchange="im_exchange", exchange_type="topic")
            logger.info(f"Initialized RabbitMQ connection: host={config.RABBITMQ_HOST}, port={config.RABBITMQ_PORT}")
        except Exception as e:
            logger.error(f"Failed to initialize RabbitMQ connection: {e}")
            time.sleep(5)
            init_rabbitmq()

    if not connection or connection.is_closed:
        init_rabbitmq()

    try:
        # Declare a generic queue for all platforms
        queue_name = "im_queue_all"
        channel.queue_declare(queue=queue_name, durable=True)
        # Bind queue to all platform topics using wildcard
        channel.queue_bind(queue=queue_name, exchange="im_exchange", routing_key="#")

        # Set prefetch count for fair dispatching
        channel.basic_qos(prefetch_count=1)

        # Callback: Process received messages
        def callback(ch, method, properties, body):
            try:
                message = json.loads(body)
                routing_key = method.routing_key  # e.g., "telegram/7890547742/status_update"
                logger.info(f"Received message from topic {routing_key}: {message}")

                # Parse platform and chat_id from routing_key
                parts = routing_key.split("/")
                if len(parts) < 3:
                    logger.error(f"Invalid routing key format: {routing_key}")
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                    return

                platform = parts[0]
                chat_id = parts[1]
                event = parts[2] if len(parts) > 2 else "unknown"

                if event != "status_update":
                    logger.info(f"Ignoring event {event} (only processing status_update)")
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                    return

                if "device_status" not in message:
                    logger.warning(f"Message does not contain device_status: {message}")
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                    return

                # Extract message details
                status = message.get("device_status")
                device_id = message.get("device_id", config.DEVICE_ID)
                bound_users = bindings.get(device_id, set())

                if not chat_id:
                    logger.error(f"No chat_id found in message: {message}")
                    ch.basic_ack(delivery_tag=method.delivery_tag)
                    return

                # Send status update to the user who triggered the command
                formatted_message = f"Device {device_id} is now {status} by user {chat_id}"
                success = send_message(chat_id, formatted_message, platform)
                if not success:
                    logger.warning(f"Failed to send status update to chat_id={chat_id} on platform={platform}")

                # Notify other bound users
                other_bound_users = bound_users - {chat_id}
                if other_bound_users:
                    logger.info(f"Notifying other bound users: {other_bound_users}")
                    for user in other_bound_users:
                        other_message = f"Device {device_id} has been {status} by user {chat_id}"
                        success = send_message(user, other_message, platform)
                        if not success:
                            logger.warning(f"Failed to notify bound user {user} on platform={platform}")

                ch.basic_ack(delivery_tag=method.delivery_tag)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse message body as JSON: {e}, body={body}")
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            except Exception as e:
                logger.error(f"Error processing message: {e}, body={body}", exc_info=True)
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

        channel.basic_consume(queue=queue_name, on_message_callback=callback, auto_ack=False)
        logger.info(f"Started consuming IM Queue for all platforms...")
        channel.start_consuming()

    except Exception as e:
        logger.error(f"Error consuming IM Queue for all platforms: {e}")
        if connection and not connection.is_closed:
            connection.close()
        time.sleep(5)
        consume_im_queue()