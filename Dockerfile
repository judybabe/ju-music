# 使用官方Python運行環境
FROM python:3.11-slim

# 安裝系統依賴
RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# 設定工作目錄
WORKDIR /app

# 複製依賴文件
COPY requirements.txt .

# 安裝Python依賴
RUN pip install --no-cache-dir -r requirements.txt

# 複製應用程式檔案
COPY . .

# 設定環境變數
ENV PYTHONUNBUFFERED=1

# 暴露端口 (某些平台需要)
EXPOSE 8080

# 啟動應用程式
CMD ["python", "bot.py"]