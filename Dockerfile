# 使用官方的 Python 基礎映像
FROM python:3.10

# 設定工作目錄
WORKDIR /app

# 複製當前目錄的內容到容器中的 /app 目錄
COPY . /app

# 安裝所需的 Python 套件
RUN pip install --no-cache-dir -r requirements.txt

# 暴露應用程式運行的端口（假設為 5000）
EXPOSE 5000

# 設定環境變數，告知 Flask 應用在公開模式下運行
ENV FLASK_RUN_HOST=0.0.0.0

ENV TELEGRAM_BOT_TOKEN='EnterYourTelegramBotTokenHere'

# 啟動應用程式
CMD ["python", "app.py"]
