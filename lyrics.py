import asyncio
import bisect
import re

import aiohttp

# LRCLIB：免費、不需金鑰的同步歌詞資料庫 (https://lrclib.net)
LRCLIB_SEARCH_URL = 'https://lrclib.net/api/search'
USER_AGENT = 'ju-music (https://github.com/judybabe/ju-music)'

# 歌詞版本和影片長度最多差幾秒，超過就視為不同版本 (時間軸會對不上)
DURATION_TOLERANCE = 10
# 每首歌最多嘗試幾組搜尋字串
MAX_QUERIES = 6

_TIMESTAMP = re.compile(r'\[(\d+):(\d+(?:\.\d+)?)\]')
_OFFSET = re.compile(r'^\[offset:\s*([+-]?\d+)\]', re.IGNORECASE)

# YouTube 標題裡和歌名無關的字
_NOISE = re.compile(
    r'official\s*(music\s*)?(video|mv|audio|visualizer)|music\s*video|lyrics?\s*video|'
    r'\blyrics?\b|\bm/?v\b|\bhd\b|\b4k\b|'
    r'官方(完整)?(版)?|公式|動態歌詞|歌詞動[画畫]|歌詞版?|完整版|高音質',
    re.IGNORECASE,
)
_BRACKETS = re.compile(r'[【「『《〈\[](.+?)[】」』》〉\]]')
_PARENS = re.compile(r'[(（][^)）]*[)）]')
_SEPARATORS = re.compile(r'[\-–—|｜/／~～・·•]+')
_ARTIST_TITLE_SPLIT = re.compile(r'\s+[\-–—|｜]\s+')
# 把「周杰倫 Jay Chou」拆成「周杰倫」和「Jay Chou」
_SCRIPT_RUNS = re.compile(r'[^\x00-\x7f]+(?:\s+[^\x00-\x7f]+)*|[\x00-\x7f]+')


def parse_lrc(text):
    """把 LRC 歌詞轉成依時間排序的 [(秒數, 歌詞), ...]"""
    offset = 0.0
    lines = []
    for raw in text.splitlines():
        raw = raw.strip()
        offset_match = _OFFSET.match(raw)
        if offset_match:
            # 正的 offset 代表歌詞要提早出現
            offset = int(offset_match.group(1)) / 1000
            continue

        stamps = _TIMESTAMP.findall(raw)
        if not stamps:
            continue
        lyric = _TIMESTAMP.sub('', raw).strip()
        for minutes, seconds in stamps:
            lines.append((int(minutes) * 60 + float(seconds) - offset, lyric))

    lines.sort(key=lambda line: line[0])
    return lines


def current_line_index(lines, position):
    """回傳播放到 position 秒時正在唱的那一句的索引，還沒開始唱則回傳 -1"""
    times = [time for time, _ in lines]
    return bisect.bisect_right(times, position) - 1


def _unique(items):
    return list(dict.fromkeys(item for item in items if item))


def _clean(text):
    text = _PARENS.sub(' ', text)
    text = _NOISE.sub(' ', text)
    text = _SEPARATORS.sub(' ', text)
    return ' '.join(text.split())


def _script_runs(text):
    return _unique(run.strip() for run in _SCRIPT_RUNS.findall(text))


def build_queries(title, hint=None):
    """從 YouTube 標題 (和使用者的搜尋字) 組出由精確到寬鬆的 LRCLIB 搜尋字串"""
    # 括號裡只有雜訊 (例：【歌詞動画/Lyric Video】) 就整個拿掉，不當成歌名
    title = _BRACKETS.sub(lambda m: m.group(0) if _clean(m.group(1)) else ' ', title)

    bracket = _BRACKETS.search(title)
    if bracket:
        # 例：周杰倫 Jay Chou【青花瓷 Blue and White Porcelain】-Official Music Video
        track = _clean(bracket.group(1))
        artist = _clean(title[:bracket.start()] + ' ' + title[bracket.end():])
    else:
        # 例：Taylor Swift - Shake It Off
        parts = _ARTIST_TITLE_SPLIT.split(title, maxsplit=1)
        if len(parts) == 2:
            artist, track = _clean(parts[0]), _clean(parts[1])
        else:
            artist, track = '', _clean(title)

    # 歌名常附英文翻譯，先只用第一段 (例：青花瓷) 搜尋
    track_options = _unique(_script_runs(track)[:1] + [track])
    artist_options = _unique(_script_runs(artist) + [artist]) if artist else []

    queries = [hint] if hint else []
    for track_option in track_options:
        for artist_option in artist_options:
            queries.append(f'{track_option} {artist_option}')
    # 只用歌名搜尋，靠長度篩掉同名的其他歌
    queries.extend(track_options)
    return _unique(queries)[:MAX_QUERIES]


def _pick_synced(results, duration):
    """挑出有同步歌詞、而且長度最接近影片的版本"""
    candidates = [
        item for item in results
        if isinstance(item, dict) and item.get('syncedLyrics') and not item.get('instrumental')
    ]
    if not duration:
        return candidates[0] if candidates else None

    candidates = [
        item for item in candidates
        if abs((item.get('duration') or 0) - duration) <= DURATION_TOLERANCE
    ]
    return min(candidates, key=lambda item: abs(item['duration'] - duration), default=None)


async def _search(session, query, retries=2):
    for attempt in range(retries + 1):
        async with session.get(LRCLIB_SEARCH_URL, params={'q': query}) as response:
            if response.status == 200:
                results = await response.json()
                return results if isinstance(results, list) else []
            # LRCLIB 忙碌時常回 503，等一下再試 (1 秒、2 秒)
            if response.status in (429, 503) and attempt < retries:
                await asyncio.sleep(attempt + 1)
                continue
            return []
    return []


async def fetch_synced_lyrics(title, duration=None, hint=None):
    """搜尋同步歌詞，找到就回傳 parse_lrc 的結果，找不到回傳 None"""
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(headers={'User-Agent': USER_AGENT}, timeout=timeout) as session:
        for query in build_queries(title, hint):
            try:
                results = await _search(session, query)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                print(f'歌詞搜尋失敗 ({query}): {e}')
                continue

            match = _pick_synced(results, duration)
            if match:
                lines = parse_lrc(match['syncedLyrics'])
                if lines:
                    return lines
    return None
