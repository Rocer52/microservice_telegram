import re
from command import enable_api, disable_api, set_status_api, Device

def parse_message(message_text: str, device: Device, chat_id: str, get_current_status) -> bool:
    """解析 Telegram 訊息並執行相應命令"""
    message_text = message_text.lower().strip()

    # 使用正則表達式解析命令，允許靈活輸入
    if re.match(r"^(enable|turn\s+on)(\s+.*)?$", message_text):
        if enable_api(device):
            send_message(chat_id, "Turning on the light!")
            return True
        else:
            send_message(chat_id, "Failed to turn on the light.")
            return False
    elif re.match(r"^(disable|turn\s+off)(\s+.*)?$", message_text):
        if disable_api(device):
            send_message(chat_id, "Turning off the light~")
            return True
        else:
            send_message(chat_id, "Failed to turn off the light.")
            return False
    elif re.match(r"^(get\s+status)(\s+.*)?$", message_text):
        current_status = get_current_status()
        send_message(chat_id, f"Current status: {current_status}")
        return True
    elif re.match(r"^set\s+status\s+(on|off)(\s+.*)?$", message_text):
        # 提取狀態值 (on 或 off)
        status = re.search(r"set\s+status\s+(on|off)", message_text).group(1)
        if set_status_api(device, status):
            send_message(chat_id, f"Status set to {status}!")
            return True
        else:
            send_message(chat_id, f"Failed to set status to {status}.")
            return False
    else:
        send_message(chat_id, "Sorry, I don't understand that message.")
        return False

def send_message(chat_id: str, text: str) -> bool:
    """發送 Telegram 訊息"""
    import requests
    import config
    url = f"{config.TELEGRAM_API_URL}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        response = requests.post(url, json=payload)
        return response.status_code == 200
    except Exception as e:
        print(f"Telegram 訊息發送失敗: {e}")
        return False