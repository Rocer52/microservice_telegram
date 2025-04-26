from flask import Flask, request
import requests
import json
import config
import logging
import IoTQbroker
import IMQbroker
import threading

# 設置日誌，記錄程式執行資訊
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# 儲存所有聊天 ID，用於群發消息
chat_ids = set()

# 函數：添加聊天 ID 到 chat_ids 集合
def add_chat_id(chat_id: str):
    chat_ids.add(chat_id)

# 函數：發送消息到 Telegram 用戶
def send_message(chat_id: str, text: str) -> bool:
    url = f"{config.TELEGRAM_API_URL}/sendMessage"  # Telegram API 發送消息的 URL
    payload = {"chat_id": chat_id, "text": text}  # 消息參數
    try:
        response = requests.post(url, json=payload)  # 發送 HTTP POST 請求
        if response.status_code == 200:
            logger.info(f"成功發送消息: chat_id={chat_id}, text={text}")
            return True
        else:
            logger.error(f"發送消息失敗: {response.text}")
            return False
    except Exception as e:
        logger.error(f"發送消息時發生錯誤: {e}")
        return False

# 函數：發送消息到 Telegram 群組（與單人消息相同）
def send_group_message(group_id: str, text: str) -> bool:
    return send_message(group_id, text)  # Telegram 的群組消息與單人消息相同

# 函數：發送消息給所有已知的聊天 ID
def send_all_message(text: str) -> bool:
    success = True
    for chat_id in chat_ids:  # 遍歷所有聊天 ID
        if not send_message(chat_id, text):  # 逐一發送消息
            success = False
    return success

# 路由：處理 Telegram Webhook 請求
@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()  # 獲取請求中的 JSON 數據
    if data is None:
        logger.error("Webhook 請求無法解析為 JSON")
        return {"ok": False, "message": "無效的 JSON"}, 400

    if 'message' not in data:
        logger.warning("Webhook 請求中沒有消息，忽略")
        return {"ok": True, "message": "請求中無消息，忽略"}, 200

    message_text = data['message'].get('text', '')  # 獲取消息內容
    chat = data['message'].get('chat')  # 獲取聊天資訊
    if not chat:
        logger.error("Webhook 請求中無聊天資訊")
        return {"ok": False, "message": "請求中無聊天資訊"}, 400

    chat_id = str(chat.get('id'))  # 獲取聊天 ID
    group_id = str(chat.get('id')) if chat.get('type') in ['group', 'supergroup'] else None  # 獲取群組 ID

    logger.info(f"收到消息: chat_id={chat_id}, group_id={group_id}, text={message_text}")

    add_chat_id(chat_id)  # 添加聊天 ID 到 chat_ids

    # 調用 IOTQbroker 解析消息並發送到 IOTQueue
    device = IoTQbroker.Device("LivingRoomLight", platform="telegram")
    iot_result = IoTQbroker.IoTParse_Message(message_text, device, chat_id, "telegram")
    if not iot_result["success"]:
        send_message(chat_id, "請輸入有效的命令")  # 命令無效時回覆
    else:
        send_message(chat_id, f"命令已接收: {message_text}")  # 命令有效時回覆

    return {"ok": True}, 200

# 路由：手動發送消息給指定用戶
@app.route('/SendMsg', methods=['GET'])
def send_message_route():
    chat_id = request.args.get('chat_id')  # 獲取聊天 ID
    message = request.args.get('message')  # 獲取消息內容
    if not chat_id or not message:
        return {"ok": False, "message": "缺少 chat_id 或 message"}, 400

    success = send_message(chat_id, message)  # 發送消息
    return {"ok": success, "message": "消息已發送" if success else "發送消息失敗"}, 200 if success else 500

# 路由：手動發送消息給指定群組
@app.route('/SendGroupMessage', methods=['GET'])
def send_group_message_route():
    group_id = request.args.get('group_id')  # 獲取群組 ID
    message = request.args.get('message')  # 獲取消息內容
    if not group_id or not message:
        return {"ok": False, "message": "缺少 group_id 或 message"}, 400

    success = send_group_message(group_id, message)  # 發送群組消息
    return {"ok": success, "message": "群組消息已發送" if success else "發送群組消息失敗"}, 200 if success else 500

# 路由：手動發送消息給所有用戶
@app.route('/SendAllMessage', methods=['GET'])
def send_all_message_route():
    message = request.args.get('message')  # 獲取消息內容
    if not message:
        return {"ok": False, "message": "缺少 message"}, 400

    success = send_all_message(message)  # 發送消息給所有用戶
    return {"ok": success, "message": "所有消息已發送" if success else "部分消息發送失敗"}, 200 if success else 500

# 主程式入口
if __name__ == "__main__":
    # 啟動 IMQbroker 消費 IMQueue 的線程
    imqbroker_thread = threading.Thread(target=IMQbroker.consume_im_queue)
    imqbroker_thread.daemon = True  # 設置為守護線程，隨主程式結束
    imqbroker_thread.start()
    logger.info("IMQbroker 已啟動，運行於獨立線程中")

    # 啟動 Flask 服務，監聽在 0.0.0.0:5000
    app.run(host="0.0.0.0", port=5000)