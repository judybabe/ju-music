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
# 有些歌詞開頭會放製作名單 (例：编曲 : 告五人)，不是歌詞，不要顯示
_CREDIT = re.compile(
    r'^(作词|作詞|作曲|编曲|編曲|制作人|製作人|监制|監製|混音|母带|母帶|和声|和聲|录音|錄音|'
    r'出品|发行|發行|词|詞|曲)\s*[:：]'
)
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
        if _CREDIT.match(lyric):
            continue
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


def build_queries(title, hints=()):
    """從 YouTube 標題 (和歌名歌手、使用者的搜尋字) 組出由精確到寬鬆的 LRCLIB 搜尋字串"""
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

    queries = list(hints)
    for track_option in track_options:
        for artist_option in artist_options:
            queries.append(f'{track_option} {artist_option}')
    # 只用歌名搜尋，靠長度篩掉同名的其他歌
    queries.extend(track_options)
    return _unique(queries)[:MAX_QUERIES]


def _candidates(results, duration):
    """有同步歌詞、而且長度和影片差不多的版本"""
    return [
        item for item in results
        if isinstance(item, dict) and item.get('syncedLyrics') and not item.get('instrumental')
        and (not duration or abs((item.get('duration') or 0) - duration) <= DURATION_TOLERANCE)
    ]


def _non_latin_ratio(text):
    letters = [ch for ch in text if ch.isalpha()]
    return sum(ord(ch) > 0x24f for ch in letters) / len(letters) if letters else 0.0


def _score(item, duration, wants_non_latin):
    """越大越好：中日韓等歌曲先比原文比例 (不要羅馬拼音)，再比長度"""
    script = round(_non_latin_ratio(item['syncedLyrics']), 1) if wants_non_latin else 0
    length_diff = abs((item.get('duration') or 0) - duration) if duration else 0
    return script, -length_diff


def _same_track(a, b):
    def key(item):
        return ''.join(ch for ch in (item.get('trackName') or '').lower() if ch.isalnum())
    ka, kb = key(a), key(b)
    return bool(ka and kb) and (ka in kb or kb in ka)


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


async def find_synced_match(session, title, duration=None, hints=()):
    """回傳最適合的 LRCLIB 歌詞資料，找不到回傳 None"""
    # 歌名、歌手或搜尋字裡有中日韓等文字，就要原文歌詞
    wants_non_latin = any(_non_latin_ratio(text) > 0 for text in (title, *hints))

    best = None
    for query in build_queries(title, hints):
        try:
            results = await _search(session, query)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            print(f'歌詞搜尋失敗 ({query}): {e}')
            continue

        candidates = _candidates(results, duration)
        if not candidates:
            continue
        pick = max(candidates, key=lambda item: _score(item, duration, wants_non_latin))
        if best is None:
            best = pick
        elif (_same_track(pick, best) and
              _non_latin_ratio(pick['syncedLyrics']) >= _non_latin_ratio(best['syncedLyrics']) + 0.1):
            # 同一首歌找到更接近原文的版本 (例：日文歌詞取代羅馬拼音)
            best = pick

        # 拉丁字母的歌，或已經是原文歌詞 (韓文歌常夾英文，所以不要求 100%)，就不用再查了
        if not wants_non_latin or _non_latin_ratio(best['syncedLyrics']) >= 0.6:
            break
    return best


async def fetch_synced_lyrics(title, duration=None, hints=()):
    """搜尋同步歌詞，找到就回傳 parse_lrc 的結果，找不到回傳 None"""
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(headers={'User-Agent': USER_AGENT}, timeout=timeout) as session:
        match = await find_synced_match(session, title, duration, hints)
    if not match:
        return None
    return parse_lrc(match['syncedLyrics']) or None
