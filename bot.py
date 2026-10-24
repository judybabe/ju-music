import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import yt_dlp
import asyncio
from collections import deque
import re
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from lyrics import fetch_synced_lyrics, current_line_index

# 載入環境變數
load_dotenv()

# 設定機器人
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix='!', intents=intents)

# 音樂佇列 - 每個伺服器都有自己的佇列
music_queues = {}

# 歌詞同步任務 - 每個伺服器同時只會有一個
lyrics_tasks = {}

# 歌詞比實際播放提早多少秒顯示，抵銷編輯訊息的網路延遲
LYRICS_LEAD = 0.3
# 兩次編輯歌詞訊息之間至少間隔幾秒，避免撞到 Discord 的頻率限制
LYRICS_MIN_EDIT_INTERVAL = 1.0

# yt-dlp 設定
ytdl_format_options = {
    'format': 'bestaudio/best',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0'
}

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)


class TimedAudio(discord.FFmpegPCMAudio):
    """記錄已送出的音訊幀數，算出實際播放到第幾秒 (不受 ffmpeg 啟動延遲影響)"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.frames = 0

    def read(self):
        data = super().read()
        if data:
            self.frames += 1
        return data

    @property
    def position(self):
        return self.frames * discord.opus.Encoder.FRAME_LENGTH / 1000

# Spotify API 設定
spotify_client_id = os.getenv('SPOTIFY_CLIENT_ID')
spotify_client_secret = os.getenv('SPOTIFY_CLIENT_SECRET')

spotify = None
if spotify_client_id and spotify_client_secret:
    try:
        spotify = spotipy.Spotify(client_credentials_manager=SpotifyClientCredentials(
            client_id=spotify_client_id,
            client_secret=spotify_client_secret
        ))
        print("✅ Spotify API 已連接")
    except Exception as e:
        print(f"⚠️ Spotify API 連接失敗: {e}")
else:
    print("⚠️ 未設定 Spotify API 憑證，將跳過 Spotify 功能")

# Spotify URL 處理函數
async def get_spotify_track_info(spotify_url):
    """從 Spotify URL 取得歌曲資訊並轉換為搜尋字串"""
    if not spotify:
        return None
    
    try:
        # 解析不同類型的 Spotify URL
        if 'track/' in spotify_url:
            track_id = spotify_url.split('track/')[1].split('?')[0]
            track = spotify.track(track_id)
            artist_names = ', '.join([artist['name'] for artist in track['artists']])
            search_query = f"{track['name']} {artist_names}"
            return search_query, track['name'], artist_names
            
        elif 'playlist/' in spotify_url:
            playlist_id = spotify_url.split('playlist/')[1].split('?')[0]
            results = spotify.playlist_tracks(playlist_id)
            tracks = []
            
            for item in results['items']:
                if item['track']:
                    track = item['track']
                    artist_names = ', '.join([artist['name'] for artist in track['artists']])
                    search_query = f"{track['name']} {artist_names}"
                    tracks.append((search_query, track['name'], artist_names))
            
            return tracks
            
        elif 'album/' in spotify_url:
            album_id = spotify_url.split('album/')[1].split('?')[0]
            results = spotify.album_tracks(album_id)
            tracks = []
            
            for track in results['items']:
                artist_names = ', '.join([artist['name'] for artist in track['artists']])
                search_query = f"{track['name']} {artist_names}"
                tracks.append((search_query, track['name'], artist_names))
                
            return tracks
            
    except Exception as e:
        print(f"Spotify API 錯誤: {e}")
        return None
        
    return None

@bot.event
async def on_ready():
    print(f'{bot.user} 已經準備好了！')

# 測試指令
@bot.command()
async def hello(ctx):
    await ctx.send(f'你好 {ctx.author.mention}！')

# 加入語音頻道
@bot.command()
async def join(ctx):
    if not ctx.author.voice:
        await ctx.send("❌ 你必須先加入一個語音頻道！")
        return
    
    channel = ctx.author.voice.channel
    if ctx.voice_client is None:
        await channel.connect()
        await ctx.send(f"✅ 已加入 {channel.name}")
    else:
        await ctx.voice_client.move_to(channel)
        await ctx.send(f"✅ 已移動到 {channel.name}")

# 離開語音頻道
@bot.command()
async def leave(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("✅ 已離開語音頻道")
        
        # 清空該伺服器的音樂佇列
        guild_id = ctx.guild.id
        if guild_id in music_queues:
            music_queues[guild_id].clear()
    else:
        await ctx.send("❌ 機器人不在任何語音頻道中")

# 播放音樂的核心函數
async def play_next(ctx):
    guild_id = ctx.guild.id
    
    if guild_id not in music_queues or not music_queues[guild_id]:
        return
    
    url = music_queues[guild_id].popleft()
    
    try:
        # 從YouTube取得音訊串流
        data = await asyncio.get_event_loop().run_in_executor(
            None, lambda: ytdl.extract_info(url, download=False)
        )
        
        if 'entries' in data:
            data = data['entries'][0]
        
        audio_url = data['url']
        title = data.get('title', '未知標題')

        # 播放音訊 (after 是在播放執行緒裡呼叫的，要交回機器人的事件迴圈執行)
        source = TimedAudio(audio_url, **ffmpeg_options)
        ctx.voice_client.play(
            source,
            after=lambda e: asyncio.run_coroutine_threadsafe(after_playing(ctx, e), bot.loop)
        )

        await ctx.send(f"🎵 正在播放: **{title}**")

        # 用使用者的搜尋字 (或 Spotify 的歌名+歌手) 幫忙找歌詞
        hint = url[len('ytsearch:'):] if url.startswith('ytsearch:') else None
        lyrics_tasks[guild_id] = asyncio.create_task(
            show_lyrics(ctx, source, title, data.get('duration'), hint)
        )

    except Exception as e:
        await ctx.send(f"❌ 播放時發生錯誤: {str(e)}")
        # 如果這首歌出錯，繼續播放下一首
        await play_next(ctx)

# 播放結束後的處理
async def after_playing(ctx, error):
    if error:
        print(f'播放錯誤: {error}')

    stop_lyrics(ctx.guild.id)

    # 播放佇列中的下一首歌
    await play_next(ctx)

# 停止目前的歌詞同步
def stop_lyrics(guild_id):
    task = lyrics_tasks.pop(guild_id, None)
    if task:
        task.cancel()

# 組出歌詞訊息：上一句變淡、正在唱的那句放大、下面預告兩句
def render_lyrics(title, lines, index):
    def text(i):
        return discord.utils.escape_markdown(lines[i][1]) or '♪'

    parts = [f"🎤 **{discord.utils.escape_markdown(title)}**"]
    if index >= 1:
        parts.append(f"-# {text(index - 1)}")
    parts.append(f"# {text(index) if index >= 0 else '♪'}")
    for i in range(index + 1, min(index + 3, len(lines))):
        parts.append(text(i))
    return "\n".join(parts)

# 抓歌詞並跟著播放進度更新訊息
async def show_lyrics(ctx, source, title, duration, hint):
    try:
        lines = await fetch_synced_lyrics(title, duration, hint)
    except Exception as e:
        print(f'歌詞抓取錯誤: {e}')
        lines = None

    if not lines:
        await ctx.send("-# 🎤 找不到這首歌的同步歌詞")
        return

    shown = current_line_index(lines, source.position + LYRICS_LEAD)
    message = await ctx.send(render_lyrics(title, lines, shown))

    while ctx.voice_client and ctx.voice_client.source is source:
        index = current_line_index(lines, source.position + LYRICS_LEAD)
        if index == shown:
            await asyncio.sleep(0.2)
            continue

        try:
            await message.edit(content=render_lyrics(title, lines, index))
        except discord.NotFound:
            # 歌詞訊息被刪掉了，就不再更新
            return
        except discord.HTTPException as e:
            print(f'更新歌詞失敗: {e}')
        shown = index
        await asyncio.sleep(LYRICS_MIN_EDIT_INTERVAL)

# 播放指令
@bot.command()
async def play(ctx, *, search):
    # 檢查機器人是否在語音頻道
    if not ctx.voice_client:
        if ctx.author.voice:
            await ctx.author.voice.channel.connect()
        else:
            await ctx.send("❌ 你必須先加入語音頻道！")
            return
    
    guild_id = ctx.guild.id
    if guild_id not in music_queues:
        music_queues[guild_id] = deque()
    
    # 檢查是否為 Spotify URL
    if 'spotify.com' in search:
        await ctx.send("🎵 正在處理 Spotify 連結...")
        spotify_info = await get_spotify_track_info(search)
        
        if not spotify_info:
            await ctx.send("❌ 無法處理此 Spotify 連結，請檢查 Spotify API 設定")
            return
        
        # 處理單首歌曲
        if isinstance(spotify_info, tuple) and len(spotify_info) == 3:
            search_query, title, artist = spotify_info
            youtube_search = f"ytsearch:{search_query}"
            music_queues[guild_id].append(youtube_search)
            await ctx.send(f"✅ 已從 Spotify 加入: **{title}** - {artist}")
            
        # 處理播放清單或專輯
        elif isinstance(spotify_info, list):
            added_count = 0
            for search_query, title, artist in spotify_info[:50]:  # 限制最多50首
                youtube_search = f"ytsearch:{search_query}"
                music_queues[guild_id].append(youtube_search)
                added_count += 1
            
            await ctx.send(f"✅ 已從 Spotify 加入 {added_count} 首歌曲到佇列")
    
    # 處理YouTube URL或一般搜尋
    else:
        # 如果不是URL，當作搜尋關鍵字
        if not (search.startswith('http://') or search.startswith('https://')):
            search = f"ytsearch:{search}"
        
        music_queues[guild_id].append(search)
        
        # 嘗試取得歌曲標題預覽
        if search.startswith('ytsearch:'):
            await ctx.send(f"✅ 已加入佇列: {search[9:]}")  # 移除 'ytsearch:' 前綴
        else:
            await ctx.send(f"✅ 已加入佇列")
    
    # 如果目前沒有播放音樂，開始播放
    if not ctx.voice_client.is_playing():
        await play_next(ctx)

# 跳過當前歌曲
@bot.command()
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("⏭️ 已跳過當前歌曲")
    else:
        await ctx.send("❌ 目前沒有播放音樂")

# 停止播放並清空佇列
@bot.command()
async def stop(ctx):
    if ctx.voice_client:
        guild_id = ctx.guild.id
        if guild_id in music_queues:
            music_queues[guild_id].clear()
        ctx.voice_client.stop()
        await ctx.send("⏹️ 已停止播放並清空佇列")
    else:
        await ctx.send("❌ 目前沒有播放音樂")

# 顯示播放佇列
@bot.command()
async def queue(ctx):
    guild_id = ctx.guild.id
    if guild_id not in music_queues or not music_queues[guild_id]:
        await ctx.send("📋 播放佇列是空的")
        return
    
    queue_list = list(music_queues[guild_id])
    queue_text = "\n".join([f"{i+1}. {song}" for i, song in enumerate(queue_list[:10])])
    
    embed = discord.Embed(title="🎵 播放佇列", description=queue_text, color=0x00ff00)
    if len(queue_list) > 10:
        embed.set_footer(text=f"... 還有 {len(queue_list) - 10} 首歌")
    
    await ctx.send(embed=embed)

# 幫助指令
@bot.command()
async def help_music(ctx):
    embed = discord.Embed(
        title="🎵 音樂機器人指令說明",
        description="以下是所有可用的指令：",
        color=0x1DB954  # Spotify 綠色
    )
    
    embed.add_field(
        name="📻 基本操作",
        value=(
            "`!join` - 加入你的語音頻道\n"
            "`!leave` - 離開語音頻道\n"
            "`!hello` - 測試機器人是否運作"
        ),
        inline=False
    )
    
    embed.add_field(
        name="🎵 播放控制",
        value=(
            "`!play <歌名/URL>` - 播放歌曲或加入佇列\n"
            "`!skip` - 跳過當前歌曲\n"
            "`!stop` - 停止播放並清空佇列\n"
            "`!queue` - 顯示播放佇列"
        ),
        inline=False
    )
    
    embed.add_field(
        name="🎧 支援的平台",
        value=(
            "✅ YouTube (直接連結或搜尋)\n"
            "✅ Spotify (歌曲、專輯、播放清單)\n"
            "✅ 關鍵字搜尋\n"
            "🎤 自動顯示同步歌詞 (資料來源: LRCLIB)"
        ),
        inline=False
    )
    
    embed.add_field(
        name="💡 使用範例",
        value=(
            "`!play 周杰倫 青花瓷`\n"
            "`!play https://open.spotify.com/track/...`\n"
            "`!play https://www.youtube.com/watch?v=...`"
        ),
        inline=False
    )
    
    embed.set_footer(text="由 Discord Music Bot 提供")
    await ctx.send(embed=embed)

# 啟動機器人
if __name__ == "__main__":
    token = os.getenv('DISCORD_TOKEN')
    if not token:
        print("錯誤：找不到 DISCORD_TOKEN，請檢查 .env 文件")
    else:
        bot.run(token)