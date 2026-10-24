# 🎵 Discord 音樂機器人

一個功能完整的Discord音樂機器人，支援YouTube和Spotify播放。

## ✨ 功能特色

- 🎵 播放YouTube音樂和影片
- 🎧 支援Spotify歌曲、專輯、播放清單
- 📱 關鍵字搜尋音樂
- 📋 音樂佇列管理
- 🎤 自動抓取同步歌詞，跟著音樂即時更新，正在唱的那句放大顯示 (資料來源: [LRCLIB](https://lrclib.net))
- 🎛️ 基本播放控制 (跳過、停止)
- 🌐 支援雲端部署

## 🎮 指令列表

| 指令 | 說明 |
|------|------|
| `!join` | 機器人加入語音頻道 |
| `!leave` | 機器人離開語音頻道 |
| `!play <歌名/URL>` | 播放音樂或加入佇列 |
| `!skip` | 跳過當前歌曲 |
| `!stop` | 停止播放並清空佇列 |
| `!queue` | 顯示播放佇列 |
| `!help_music` | 顯示所有指令 |

## 🚀 快速開始

### 1. 克隆專案
```bash
git clone <你的repo地址>
cd discord-music-bot
```

### 2. 安裝依賴
```bash
pip install -r requirements.txt
```

### 3. 設定環境變數
複製 `.env.example` 為 `.env` 並填入你的資訊：
```bash
cp .env.example .env
```

編輯 `.env` 文件：
```env
DISCORD_TOKEN=你的Discord機器人Token
SPOTIFY_CLIENT_ID=你的Spotify客戶端ID (可選)
SPOTIFY_CLIENT_SECRET=你的Spotify客戶端密鑰 (可選)
```

### 4. 運行機器人
```bash
python bot.py
```

## 🔧 取得必要的Token

### Discord Bot Token
1. 前往 [Discord Developer Portal](https://discord.com/developers/applications)
2. 創建新應用程式
3. 前往 "Bot" 頁面
4. 複製 Token

### Spotify API (可選)
1. 前往 [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
2. 創建新應用程式
3. 取得 Client ID 和 Client Secret

## 🐳 Docker 部署

```bash
# 建構映像
docker build -t discord-music-bot .

# 運行容器
docker run -d --name music-bot discord-music-bot
```

## ☁️ 雲端部署

### Google Cloud Run
```bash
gcloud run deploy discord-music-bot --source . --platform managed
```

### Railway / Render
直接連接GitHub倉庫即可自動部署。

## 📋 系統需求

- Python 3.8+
- FFmpeg (音訊處理)
- 穩定的網路連線

## 🤝 貢獻

歡迎提交Issue和Pull Request！

## 📄 授權

MIT License