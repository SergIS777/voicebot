import asyncio
import aiohttp
from aiohttp import web
import json
import yaml
import os
import time
import uuid
import logging
import subprocess
import re
from clean_tts import clean_for_tts
import struct
import wave
import io

logging.basicConfig(level=logging.WARNING, format='%(asctime)s %(levelname)s: %(message)s')
logger = logging.getLogger("voicebot")

# ─── Подключения ───

ARI_URL = "http://localhost:8088"
ARI_USER = "n8n"
ARI_PASS = "YOUR_ARI_PASSWORD"
ARI_APP = "voicebot"

WHISPER_URL = "https://api.groq.com/openai"
GROQ_KEY = os.getenv("GROQ_API_KEY", "")
WHISPER_MODEL = "whisper-large-v3-turbo"
SILERO_TTS_URL = "http://localhost:8200"
EDGE_TTS_URL = "http://localhost:8201"

MS_CHAT_URL = "https://api-inference.modelscope.ai/v1/chat/completions"
MODELSCOPE_TOKEN = os.getenv("MODELSCOPE_TOKEN", "")

AUDIO_DIR = "/tmp/voicebot"
os.makedirs(AUDIO_DIR, exist_ok=True)


# ─── Загрузка конфига ───
config_name = os.getenv("VOICEBOT_CONFIG", "dental")
_base = os.path.dirname(os.path.abspath(__file__))
_candidates = [os.path.join(_base, "..", "configs", f"{config_name}.yaml"), os.path.join(_base, "configs", f"{config_name}.yaml")]
config_path = next((p for p in _candidates if os.path.exists(p)), _candidates[0])
try:
    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    logger.info(f"Loaded config: {config_name}")
except Exception as e:
    logger.warning(f"Failed to load config {config_name}: {e}, using defaults")
    config = {}

# ─── Настройки ───

settings = {
    "system_prompt": config.get("system_prompt", "Ты — голосовой ассистент. Отвечай кратко по телефону."),
    "llm_model": config.get("llm_model", "Qwen/Qwen3-30B-A3B-Instruct-2507"),
    "llm_temperature": config.get("llm_temperature", 0.7),
    "llm_max_tokens": config.get("llm_max_tokens", 80),
    "tts_engine": "edge",
    "silero_voice": "xenia",
    "silero_rate": 1.0,
    "silero_pitch": 0,
    "edge_voice": "svetlana",
    "edge_rate": "+12%",
    "edge_pitch": "+8Hz",
    "greeting": config.get("greeting", "Здравствуйте! Чем могу помочь?"),
    "error_message": config.get("error_message", "Извините, не расслышала. Повторите, пожалуйста."),
    "thinking_message": config.get("thinking_message", "Секунду..."),
    "goodbye_message": config.get("goodbye_message", "До свидания! Хорошего дня."),
    "budget_exhausted_message": config.get("budget_exhausted_message", "Извините, сервис временно недоступен. Попробуйте позже. До свидания."),
    "record_max_seconds": 10,
    "record_silence_seconds": 3,
    "max_call_duration": 300,
    "max_empty_stt": 10,
    "max_conversation_turns": 20,
    "enable_thinking_sound": True,
    "vad_enabled": True,
    "vad_rms_threshold": 200,
    "vad_min_active_frames": 2,
    "vad_frame_ms": 20,
    "vad_min_duration": 0.15,
}

channels = {}
_tts_cache = {}
_tts_cache_lock = asyncio.Lock()

# ─── Фильтр галлюцинаций ───

HALLUCINATION_KEYWORDS = [
    "субтитр", "корректор", "редактор", "переводчик",
    "олзоева", "кулакова", "синецкая", "негода",
    "титры", "монтаж", "озвучка", "озвучивание",
    "подписывайтесь", "подпишитесь", "канал", "лайк",
    "ставьте лайк", "колокольчик", "уведомления",
    "не забудьте подписаться", "подписка",
    "спасибо за просмотр", "спасибо за внимание",
    "в следующем видео", "в этом видео",
    "напишите в комментариях", "комментарии",
    "смотрите также", "ссылка в описании",
    "описание под видео", "ссылка внизу",
    "всем привет", "дорогие друзья", "дорогие зрители",
    "с вами был", "с вами была", "с вами",
    "меня зовут", "это канал",
    "до новых встреч", "до скорых встреч",
    "music", "аплодисменты", "смех", "музыка",
    "applause", "laughter", "звук", "шум",
    "тишина", "silence",
    "продолжение следует", "конец фильма", "конец серии",
    "продолжение в следующем", "следующая серия",
    "следующей серии", "в следующей серии",
    "разговор по телефону", "на русском языке",
    "телефонный разговор", "запись разговора",
    "amara.org", "www.", ".ru", ".com", ".org", "http",
    "благодарю за внимание", "спасибо за внимание",
    "до свидания дорогие", "берегите себя",
    "хорошего дня", "всего доброго", "всего хорошего",
    "приятного просмотра", "приятного прослушивания",
    "игорь негода", "фондю", "ку-ку", "тик-так",
    "бла-бла-бла", "та-да", "ля-ля",
    "ааа", "ммм", "эээ", "ууу", "ооо",
    "beep", "бип", "сигнал", "гудок",
    "помехи", "шипение", "треск",
]

GOODBYE_PHRASES = [
    "до свидания", "пока", "всего доброго", "спасибо пока",
    "всё спасибо", "всё понятно спасибо", "хватит",
    "достаточно", "конец", "завершить", "отбой",
    "до свиданья", "давай пока", "ну всё", "ладно пока",
    "покеда", "пока пока", "чао", "бывай",
    "спасибо всё", "спасибо это всё", "больше ничего",
    "ничего больше", "всё хорошо", "всё ясно",
    "понятно спасибо", "ясно спасибо",
]

MEANINGLESS_PATTERNS = [
    r'^[а-яё]{1,2}$',
    r'^(э{2,}|м{2,}|а{2,}|у{2,})$',
    r'^[\s\.\,\!\?\-\—\…]+$',
    r'(.)\1{4,}',
    r'^\d+$',
]


def is_hallucination(text):
    if not text or len(text) < 2:
        return True
    text_lower = text.lower().strip()
    for keyword in HALLUCINATION_KEYWORDS:
        if keyword in text_lower:
            return True
    for pattern in MEANINGLESS_PATTERNS:
        if re.search(pattern, text_lower):
            return True
    words = text_lower.split()
    if len(words) <= 1 and len(text_lower) < 5:
        return True
    if text.isupper() and len(text) > 10:
        return True
    subtitle_patterns = [
        r'редактор\s+\w+\s+\w+\.\w+',
        r'корректор\s+\w+\.\w+',
        r'переводчик\s+\w+',
        r'перевод\s+и\s+редакт',
    ]
    for pattern in subtitle_patterns:
        if re.search(pattern, text_lower):
            return True
    for word in words:
        if len(word) > 25:
            return True
    return False


def is_goodbye(text):
    if not text:
        return False
    text_lower = text.lower().strip()
    for phrase in GOODBYE_PHRASES:
        if phrase in text_lower:
            return True
    return False


def is_meaningless(text):
    if not text:
        return True
    text_lower = text.lower().strip()
    words = text_lower.split()
    clean = re.sub(r'[^\w\s]', '', text_lower)
    if len(words) < 2 and len(clean) < 8:
        return True
    interjections = {
        "да", "нет", "ага", "угу", "ну",
        "эм", "мм", "хм", "э", "а", "о",
    }
    if all(w in interjections for w in words):
        return True
    return False


# ─── VAD ───

def check_voice_activity(audio_data):
    threshold = settings["vad_rms_threshold"]
    min_active = settings["vad_min_active_frames"]
    frame_ms = settings["vad_frame_ms"]
    min_dur = settings["vad_min_duration"]

    try:
        buf = io.BytesIO(audio_data)
        with wave.open(buf, "rb") as wf:
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)

        duration = n_frames / framerate if framerate > 0 else 0

        if duration < min_dur:
            return {
                "has_voice": False,
                "active_frames": 0,
                "total_frames": 0,
                "max_rms": 0,
                "avg_rms": 0,
                "duration_sec": round(duration, 2),
                "reason": f"too_short ({duration:.2f}s < {min_dur}s)",
            }

        if sampwidth != 2:
            return {
                "has_voice": True,
                "active_frames": 0,
                "total_frames": 0,
                "max_rms": 0,
                "avg_rms": 0,
                "duration_sec": round(duration, 2),
                "reason": f"unsupported_format (sampwidth={sampwidth})",
            }

        fmt = f"<{len(raw) // 2}h"
        samples = list(struct.unpack(fmt, raw))

        if n_channels == 2:
            samples = samples[::2]

        frame_size = int(framerate * frame_ms / 1000)
        if frame_size == 0:
            frame_size = 1

        active_frames = 0
        total_frames = 0
        max_rms = 0
        rms_sum = 0

        for i in range(0, len(samples), frame_size):
            chunk = samples[i:i + frame_size]
            if len(chunk) < frame_size // 2:
                break
            sq_sum = sum(s * s for s in chunk)
            rms = (sq_sum / len(chunk)) ** 0.5
            total_frames += 1
            rms_sum += rms
            if rms > max_rms:
                max_rms = rms
            if rms > threshold:
                active_frames += 1

        avg_rms = rms_sum / total_frames if total_frames > 0 else 0
        has_voice = active_frames >= min_active

        reason = ""
        if not has_voice:
            if active_frames == 0:
                reason = f"silence (max_rms={max_rms:.0f} < {threshold})"
            else:
                reason = (
                    f"too_quiet ({active_frames}/{min_active} active frames)"
                )

        return {
            "has_voice": has_voice,
            "active_frames": active_frames,
            "total_frames": total_frames,
            "max_rms": round(max_rms, 1),
            "avg_rms": round(avg_rms, 1),
            "duration_sec": round(duration, 2),
            "reason": reason,
        }

    except Exception as e:
        logger.warning(f"VAD check error: {e}")
        return {
            "has_voice": True,
            "active_frames": 0,
            "total_frames": 0,
            "max_rms": 0,
            "avg_rms": 0,
            "duration_sec": 0,
            "reason": f"check_error: {str(e)[:50]}",
        }


# ─── Дневной бюджет токенов ───

class DailyTokenBudget:
    def __init__(self, daily_limit=200000, safety_margin=0.9):
        self.daily_limit = daily_limit
        self.safety_limit = int(daily_limit * safety_margin)
        self.tokens_used = 0
        self.requests_today = 0
        self.requests_blocked = 0
        self.day_start = self._today()
        self._lock = asyncio.Lock()

    def _today(self):
        return time.strftime("%Y-%m-%d")

    async def _check_day_reset(self):
        today = self._today()
        if today != self.day_start:
            logger.info(
                f"NEW DAY. Yesterday ({self.day_start}): "
                f"{self.tokens_used} tokens, {self.requests_today} requests, "
                f"{self.requests_blocked} blocked"
            )
            self.tokens_used = 0
            self.requests_today = 0
            self.requests_blocked = 0
            self.day_start = today

    async def can_spend(self, estimated_tokens=500):
        async with self._lock:
            await self._check_day_reset()
            if self.tokens_used + estimated_tokens > self.safety_limit:
                self.requests_blocked += 1
                pct = self.tokens_used / self.safety_limit * 100
                logger.error(
                    f"DAILY BUDGET LIMIT: {self.tokens_used}/{self.safety_limit} "
                    f"({pct:.0f}%), blocked #{self.requests_blocked}"
                )
                return False
            return True

    async def record_usage(self, tokens):
        async with self._lock:
            await self._check_day_reset()
            self.tokens_used += tokens
            self.requests_today += 1
            remaining = self.safety_limit - self.tokens_used
            pct = self.tokens_used / self.safety_limit * 100
            logger.info(
                f"Tokens: +{tokens} = {self.tokens_used}/{self.safety_limit} "
                f"({pct:.0f}%, {remaining} left) [req #{self.requests_today}]"
            )
            if pct >= 95:
                logger.error(f"TOKEN BUDGET CRITICAL: {pct:.0f}%!")
            elif pct >= 90:
                logger.warning(f"Token budget HIGH: {pct:.0f}%")
            elif pct >= 75:
                logger.warning(f"Token budget: {pct:.0f}%")

    def get_stats(self):
        pct = (
            self.tokens_used / self.safety_limit * 100
            if self.safety_limit > 0 else 0
        )
        return {
            "day": self.day_start,
            "tokens_used": self.tokens_used,
            "tokens_limit": self.safety_limit,
            "tokens_remaining": max(0, self.safety_limit - self.tokens_used),
            "requests_today": self.requests_today,
            "requests_blocked": self.requests_blocked,
            "pct_used": round(pct, 1),
        }


token_budget = DailyTokenBudget(daily_limit=200000, safety_margin=0.9)


# ─── Rate Limiter ───

class AdaptiveRateLimiter:
    def __init__(
        self,
        min_interval=1.0,
        max_per_minute=8,
        backoff_base=3.0,
        backoff_max=60.0,
        backoff_multiplier=2.0,
    ):
        self.min_interval = min_interval
        self.max_per_minute = max_per_minute
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.backoff_multiplier = backoff_multiplier
        self._last_call = 0
        self._calls = []
        self._current_backoff = 0
        self._consecutive_429 = 0
        self._total_429 = 0

    async def wait_if_needed(self):
        now = time.time()
        self._calls = [t for t in self._calls if now - t < 60]
        if len(self._calls) >= self.max_per_minute:
            oldest = self._calls[0]
            wait = 60 - (now - oldest) + 0.5
            if wait > 0:
                logger.warning(
                    f"Rate limit: {len(self._calls)}/{self.max_per_minute}/min, "
                    f"wait {wait:.1f}s"
                )
                await asyncio.sleep(wait)
                now = time.time()
                self._calls = [t for t in self._calls if now - t < 60]
        elapsed = now - self._last_call
        if elapsed < self.min_interval:
            await asyncio.sleep(self.min_interval - elapsed)
        if self._current_backoff > 0:
            logger.warning(f"Backoff: {self._current_backoff:.1f}s")
            await asyncio.sleep(self._current_backoff)
        self._last_call = time.time()
        self._calls.append(self._last_call)

    def on_success(self):
        self._consecutive_429 = 0
        self._current_backoff = 0

    def on_rate_limit(self):
        self._consecutive_429 += 1
        self._total_429 += 1
        if self._current_backoff == 0:
            self._current_backoff = self.backoff_base
        else:
            self._current_backoff = min(
                self._current_backoff * self.backoff_multiplier,
                self.backoff_max,
            )
        logger.warning(
            f"429 #{self._consecutive_429} (total: {self._total_429}), "
            f"backoff: {self._current_backoff:.1f}s"
        )

    def get_stats(self):
        return {
            "calls_last_minute": len(
                [t for t in self._calls if time.time() - t < 60]
            ),
            "current_backoff": self._current_backoff,
            "consecutive_429": self._consecutive_429,
            "total_429": self._total_429,
        }


rate_limiter = AdaptiveRateLimiter(
    min_interval=1.0,
    max_per_minute=8,
    backoff_base=3.0,
    backoff_max=60.0,
    backoff_multiplier=2.0,
)


# ─── FFmpeg ───

def ffmpeg_convert(data, out_rate):
    fi = f"{AUDIO_DIR}/ci_{uuid.uuid4().hex[:8]}.wav"
    fo = f"{AUDIO_DIR}/co_{uuid.uuid4().hex[:8]}.wav"
    try:
        with open(fi, "wb") as f:
            f.write(data)
        r = subprocess.run(
            [
                "ffmpeg", "-y", "-i", fi,
                "-ar", str(out_rate), "-ac", "1",
                "-sample_fmt", "s16", "-f", "wav", fo,
            ],
            capture_output=True,
            timeout=10,
        )
        if r.returncode == 0 and os.path.exists(fo) and os.path.getsize(fo) > 500:
            with open(fo, "rb") as f:
                return f.read()
        logger.warning(f"ffmpeg rc={r.returncode}")
        return data
    except subprocess.TimeoutExpired:
        logger.warning("ffmpeg timeout")
        return data
    except Exception as e:
        logger.warning(f"ffmpeg err: {e}")
        return data
    finally:
        for p in [fi, fo]:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


async def ffmpeg_async(data, out_rate):
    return await asyncio.get_event_loop().run_in_executor(
        None, ffmpeg_convert, data, out_rate
    )


# ─── Состояние канала ───

class ChannelState:
    def __init__(self, cid):
        self.cid = cid
        self.conversation = [
            {"role": "system", "content": settings["system_prompt"]}
        ]
        self.playback_file = None
        self.rec_name = None
        self.playing = False
        self.t_start = time.time()
        self.empty_stt_count = 0
        self.turn_count = 0
        self.hanging_up = False
        self.current_playback_id = None
        self.vad_skips = 0
        self.vad_total = 0

    def call_duration(self):
        return time.time() - self.t_start

    def is_expired(self):
        return self.call_duration() > settings["max_call_duration"]

    def too_many_empty(self):
        return self.empty_stt_count >= settings["max_empty_stt"]

    def too_many_turns(self):
        return self.turn_count >= settings["max_conversation_turns"]


# ─── HTTP API ───

async def api_get_settings(req):
    return web.json_response(settings)


async def api_set_settings(req):
    data = await req.json()
    changed = []
    for k in data:
        if k in settings:
            old = settings[k]
            settings[k] = data[k]
            if old != data[k]:
                changed.append(k)
    if changed:
        logger.info(f"Settings updated: {changed}")
    return web.json_response({"ok": True, "changed": changed, "settings": settings})


async def api_calls(req):
    calls_info = []
    for cid, state in channels.items():
        calls_info.append({
            "channel": cid[:8],
            "duration": round(state.call_duration()),
            "turns": state.turn_count,
            "empty_stt": state.empty_stt_count,
            "vad_skips": state.vad_skips,
            "vad_total": state.vad_total,
            "playing": state.playing,
        })
    return web.json_response({"count": len(channels), "calls": calls_info})


async def api_health(req):
    return web.json_response({
        "status": "running",
        "calls": len(channels),
        "rate_limiter": rate_limiter.get_stats(),
        "token_budget": token_budget.get_stats(),
        "tts_cache_size": len(_tts_cache),
        "vad_settings": {
            "enabled": settings["vad_enabled"],
            "rms_threshold": settings["vad_rms_threshold"],
            "min_active_frames": settings["vad_min_active_frames"],
            "min_duration": settings["vad_min_duration"],
        },
    })


# ─── TTS ───

async def tts_synthesize(s, text):
    engine = settings["tts_engine"]

    async def _call_engine(eng):
        if eng == "edge":
            return await s.get(
                f"{EDGE_TTS_URL}/tts",
                params={
                    "text": text,
                    "voice": settings["edge_voice"],
                    "rate": settings["edge_rate"],
                    "pitch": settings["edge_pitch"],
                },
                timeout=aiohttp.ClientTimeout(total=10),
            )
        else:
            return await s.get(
                f"{SILERO_TTS_URL}/tts",
                params={
                    "text": text,
                    "voice": settings["silero_voice"],
                    "sample_rate": 8000,
                },
                timeout=aiohttp.ClientTimeout(total=10),
            )

    fallback = "silero" if engine == "edge" else "edge"
    for eng in [engine, fallback]:
        try:
            r = await _call_engine(eng)
            raw = await r.read()
            if raw and len(raw) > 500:
                return await ffmpeg_async(raw, 8000)
        except Exception as e:
            logger.error(f"TTS {eng} err: {e}")
    return None


async def tts_cached(s, text, cache_key=None):
    key = cache_key or text
    async with _tts_cache_lock:
        if key in _tts_cache:
            return _tts_cache[key]
    audio = await tts_synthesize(s, text)
    if audio and cache_key:
        async with _tts_cache_lock:
            _tts_cache[key] = audio
            if len(_tts_cache) > 50:
                oldest = next(iter(_tts_cache))
                del _tts_cache[oldest]
    return audio


# ─── ARI helpers ───

async def ari_answer(s, cid):
    try:
        await s.post(
            f"{ARI_URL}/ari/channels/{cid}/answer",
            auth=aiohttp.BasicAuth(ARI_USER, ARI_PASS),
        )
    except Exception as e:
        logger.error(f"Answer err: {e}")


async def ari_record(s, cid):
    state = channels.get(cid)
    if not state or state.playing or state.hanging_up:
        return
    name = f"rec_{uuid.uuid4().hex[:8]}"
    state.rec_name = name
    try:
        r = await s.post(
            f"{ARI_URL}/ari/channels/{cid}/record",
            auth=aiohttp.BasicAuth(ARI_USER, ARI_PASS),
            params={
                "name": name,
                "format": "wav",
                "maxDurationSeconds": settings["record_max_seconds"],
                "maxSilenceSeconds": settings["record_silence_seconds"],
                "beep": "false",
                "terminateOn": "none",
                "ifExists": "overwrite",
            },
        )
        if r.status == 201:
            logger.info(
                f"[{cid[:8]}] Listening "
                f"(max {settings['record_max_seconds']}s, "
                f"silence {settings['record_silence_seconds']}s)"
            )
        else:
            logger.error(f"[{cid[:8]}] Record fail: {await r.text()}")
    except Exception as e:
        logger.error(f"Record err: {e}")


async def ari_play(s, cid, audio, playback_id=None):
    state = channels.get(cid)
    if not state or not audio or len(audio) < 500:
        return False
    fname = f"r_{uuid.uuid4().hex[:8]}"
    path = f"{AUDIO_DIR}/{fname}.wav"
    with open(path, "wb") as f:
        f.write(audio)
    state.playback_file = path
    state.playing = True
    pb_id = playback_id or uuid.uuid4().hex[:12]
    state.current_playback_id = pb_id
    try:
        r = await s.post(
            f"{ARI_URL}/ari/channels/{cid}/play/{pb_id}",
            auth=aiohttp.BasicAuth(ARI_USER, ARI_PASS),
            params={"media": f"sound:{AUDIO_DIR}/{fname}"},
        )
        if r.status == 201:
            return True
        state.playing = False
        state.current_playback_id = None
        return False
    except Exception:
        state.playing = False
        state.current_playback_id = None
        return False


async def ari_hangup(s, cid, reason="normal"):
    try:
        await s.delete(
            f"{ARI_URL}/ari/channels/{cid}",
            auth=aiohttp.BasicAuth(ARI_USER, ARI_PASS),
            params={"reason_code": reason},
        )
    except Exception as e:
        logger.error(f"Hangup err: {e}")


# ─── STT ───

async def stt(s, audio):
    try:
        audio_16k = await ffmpeg_async(audio, 16000)
        form = aiohttp.FormData()
        form.add_field(
            "file", audio_16k,
            filename="a.wav",
            content_type="audio/wav",
        )
        form.add_field("model", WHISPER_MODEL)
        form.add_field("language", "ru")
        r = await s.post(
            f"{WHISPER_URL}/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {GROQ_KEY}"},
            data=form,
            timeout=aiohttp.ClientTimeout(total=30),
        )
        res = await r.json()
        text = res.get("text", "").strip()
        if is_hallucination(text):
            if text:
                logger.warning(f"Hallucination filtered: '{text}'")
            return ""
        return text
    except asyncio.TimeoutError:
        logger.error("STT TIMEOUT (30s)")
        return ""
    except Exception as e:
        logger.error(f"STT err: {e}")
        return ""


# ─── LLM ───

def is_bad_answer(text):
    """Фильтр мусорных/ошибочных ответов LLM."""
    if not text or not text.strip():
        return True
    t = text.strip().lower()
    if len(t) < 2:
        return True
    bad = ["http://", "https://", "internal server error", "traceback",
           "<html", "<!doctype", "error code", "status code", "bad gateway",
           "service unavailable", "rate limit exceeded"]
    return any(p in t for p in bad)


async def llm(s, conv):
    estimated = (
        sum(len(m["content"]) // 3 for m in conv[-8:])
        + settings["llm_max_tokens"]
    )
    if not await token_budget.can_spend(estimated):
        return "__BUDGET_EXHAUSTED__"

    await rate_limiter.wait_if_needed()

    payload = {
        "model": settings["llm_model"],
        "messages": conv[-8:],
        "max_tokens": settings["llm_max_tokens"],
        "temperature": settings["llm_temperature"],
    }
    headers = {
        "Authorization": f"Bearer {MODELSCOPE_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        r = await s.post(
            MS_CHAT_URL,
            headers=headers,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=15),
        )
        if r.status == 429:
            rate_limiter.on_rate_limit()
            await rate_limiter.wait_if_needed()
            r = await s.post(
                MS_CHAT_URL,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15),
            )
            if r.status == 429:
                rate_limiter.on_rate_limit()
                logger.error("LLM 429 twice")
                return None

        if r.status != 200:
            body = await r.text()
            logger.error(f"LLM {r.status}: {body[:200]}")
            return None

        ct = r.headers.get("Content-Type", "")
        if "json" not in ct:
            logger.error(f"LLM bad content-type: {ct}")
            return None

        res = await r.json()
        choices = res.get("choices")
        if not choices:
            logger.error("LLM empty/null choices")
            return None

        rate_limiter.on_success()

        usage = res.get("usage", {})
        total_tokens = usage.get("total_tokens", 0)
        if total_tokens > 0:
            await token_budget.record_usage(total_tokens)
        else:
            await token_budget.record_usage(estimated)

        content = choices[0]["message"]["content"]
        if is_bad_answer(content):
            logger.error(f"LLM garbage filtered: {content[:100]!r}")
            return None
        return content

    except asyncio.TimeoutError:
        logger.error("LLM TIMEOUT (15s)")
        return None
    except Exception as e:
        logger.error(f"LLM err: {e}")
        return None


# ─── Вспомогательные ───

async def say_and_listen(s, cid, text, cache_key=None):
    audio = await tts_cached(s, text, cache_key=cache_key)
    if audio:
        if not await ari_play(s, cid, audio):
            await ari_record(s, cid)
    else:
        await ari_record(s, cid)


async def say_and_hangup(s, cid, text, cache_key=None):
    state = channels.get(cid)
    if not state:
        return
    state.hanging_up = True
    audio = await tts_cached(s, text, cache_key=cache_key)
    if audio:
        if await ari_play(s, cid, audio):
            return
    await ari_hangup(s, cid)


async def _delete_recording(s, rec):
    try:
        await s.delete(
            f"{ARI_URL}/ari/recordings/stored/{rec}",
            auth=aiohttp.BasicAuth(ARI_USER, ARI_PASS),
        )
    except Exception:
        pass


async def _cleanup_file(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


async def _play_thinking_if_slow(s, cid):
    try:
        await asyncio.sleep(2.0)
        state = channels.get(cid)
        if state and not state.playing and not state.hanging_up:
            audio = await tts_cached(
                s, settings["thinking_message"], cache_key="thinking"
            )
            if audio:
                await ari_play(s, cid, audio)
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


# ─── Обработчики ARI ───

async def on_start(s, ev):
    try:
        cid = ev["channel"]["id"]
        who = ev["channel"].get("caller", {}).get("number", "?")
        logger.info(f"=== CALL from {who} ===")
        channels[cid] = ChannelState(cid)
        await ari_answer(s, cid)
        await asyncio.sleep(0.3)
        await say_and_listen(s, cid, settings["greeting"], cache_key="greeting")
    except Exception as e:
        logger.error(f"on_start err: {e}")


async def on_rec_done(s, ev):
    cid = None
    try:
        rec = ev["recording"]["name"]
        uri = ev["recording"].get("target_uri", "")
        cid = uri.replace("channel:", "")
        dur = ev["recording"].get("duration", 0)
        state = channels.get(cid)
        if not state:
            return
        state.rec_name = None
        logger.info(f"[{cid[:8]}] Recorded {dur}s")

        if state.is_expired():
            logger.info(f"[{cid[:8]}] Call expired")
            await say_and_hangup(
                s, cid, settings["goodbye_message"], cache_key="goodbye"
            )
            return

        # Скачиваем аудио
        try:
            r = await s.get(
                f"{ARI_URL}/ari/recordings/stored/{rec}/file",
                auth=aiohttp.BasicAuth(ARI_USER, ARI_PASS),
            )
            if r.status != 200:
                logger.error(f"[{cid[:8]}] Download fail: {r.status}")
                await ari_record(s, cid)
                return
            audio = await r.read()
        except Exception as e:
            logger.error(f"[{cid[:8]}] Download err: {e}")
            await ari_record(s, cid)
            return

        asyncio.create_task(_delete_recording(s, rec))

        # VAD проверка
        state.vad_total += 1

        if settings["vad_enabled"]:
            vad = check_voice_activity(audio)
            logger.info(
                f"[{cid[:8]}] VAD: voice={vad['has_voice']}, "
                f"active={vad['active_frames']}/{vad['total_frames']}, "
                f"max_rms={vad['max_rms']:.0f}, "
                f"dur={vad['duration_sec']}s"
            )
            if not vad["has_voice"]:
                state.vad_skips += 1
                logger.info(
                    f"[{cid[:8]}] No voice: {vad['reason']} "
                    f"(skipped {state.vad_skips}/{state.vad_total})"
                )
                state.empty_stt_count += 1
                if state.too_many_empty():
                    logger.info(f"[{cid[:8]}] Too many silence, goodbye")
                    await say_and_hangup(
                        s, cid,
                        settings["goodbye_message"],
                        cache_key="goodbye",
                    )
                    return
                await ari_record(s, cid)
                return

        # PIPELINE: STT -> LLM -> TTS

        t0 = time.time()
        text = await stt(s, audio)
        t1 = time.time()
        logger.info(f"[{cid[:8]}] STT ({t1 - t0:.1f}s): '{text}'")

        if not text:
            state.empty_stt_count += 1
            if state.too_many_empty():
                logger.info(f"[{cid[:8]}] Too many empty STT, goodbye")
                await say_and_hangup(
                    s, cid,
                    settings["goodbye_message"],
                    cache_key="goodbye",
                )
                return
            if state.empty_stt_count >= 3:
                await asyncio.sleep(1)
            await ari_record(s, cid)
            return

        state.empty_stt_count = 0

        # Проверка бессмысленных фраз
        if is_meaningless(text):
            logger.info(
                f"[{cid[:8]}] Meaningless, skip LLM: '{text}'"
            )
            await ari_record(s, cid)
            return

        # Прощание
        if is_goodbye(text):
            logger.info(f"[{cid[:8]}] User goodbye: '{text}'")
            await say_and_hangup(
                s, cid,
                settings["goodbye_message"],
                cache_key="goodbye",
            )
            return

        # Лимит реплик
        state.turn_count += 1
        if state.too_many_turns():
            logger.info(f"[{cid[:8]}] Max turns reached")
            await say_and_hangup(
                s, cid,
                "Мы общаемся уже долго. Перезвоните, если нужна помощь. "
                "До свидания!",
            )
            return

        state.conversation.append({"role": "user", "content": text})

        # Думает...
        thinking_task = None
        if settings["enable_thinking_sound"]:
            thinking_task = asyncio.create_task(
                _play_thinking_if_slow(s, cid)
            )

        # LLM
        t2 = time.time()
        answer = await llm(s, state.conversation)
        t3 = time.time()

        if thinking_task and not thinking_task.done():
            thinking_task.cancel()

        # Бюджет исчерпан
        if answer == "__BUDGET_EXHAUSTED__":
            logger.error(f"[{cid[:8]}] Daily budget exhausted!")
            state.conversation.pop()
            await say_and_hangup(
                s, cid,
                settings["budget_exhausted_message"],
                cache_key="budget_exhausted",
            )
            return

        # LLM ошибка
        if answer is None:
            logger.warning(f"[{cid[:8]}] LLM failed ({t3 - t2:.1f}s)")
            state.conversation.pop()
            await say_and_listen(
                s, cid, settings["error_message"], cache_key="error"
            )
            return

        answer = clean_for_tts(answer)
        logger.info(f"[{cid[:8]}] LLM ({t3 - t2:.1f}s): '{answer}'")
        state.conversation.append({"role": "assistant", "content": answer})

        # Ждём пока thinking доиграет
        if state.playing:
            for _ in range(50):
                if not state.playing:
                    break
                await asyncio.sleep(0.1)

        # TTS
        t4 = time.time()
        audio_out = await tts_synthesize(s, answer)
        t5 = time.time()
        logger.info(
            f"[{cid[:8]}] TTS ({t5 - t4:.1f}s) "
            f"TOTAL ({t5 - t0:.1f}s)"
        )

        if audio_out:
            if not await ari_play(s, cid, audio_out):
                await ari_record(s, cid)
        else:
            await say_and_listen(
                s, cid, settings["error_message"], cache_key="error"
            )

    except Exception as e:
        logger.error(f"Pipeline err: {e}")
        if cid:
            try:
                await ari_record(s, cid)
            except Exception:
                pass


async def on_play_done(s, ev):
    try:
        uri = ev.get("playback", {}).get("target_uri", "")
        cid = uri.replace("channel:", "")
        state = channels.get(cid)
        if not state:
            return
        state.playing = False
        state.current_playback_id = None
        await _cleanup_file(state.playback_file)
        state.playback_file = None
        if state.hanging_up:
            logger.info(f"[{cid[:8]}] Goodbye played, hanging up")
            await ari_hangup(s, cid)
            return
        await ari_record(s, cid)
    except Exception as e:
        logger.error(f"on_play_done err: {e}")


async def on_end(s, ev):
    try:
        cid = ev["channel"]["id"]
        state = channels.get(cid)
        dur = round(state.call_duration()) if state else 0
        turns = state.turn_count if state else 0
        vad_skip_pct = 0
        if state and state.vad_total > 0:
            vad_skip_pct = round(
                state.vad_skips / state.vad_total * 100
            )
        if state:
            await _cleanup_file(state.playback_file)
        channels.pop(cid, None)
        logger.info(
            f"=== HANGUP ({dur}s, {turns} turns, "
            f"VAD skipped {vad_skip_pct}%) ==="
        )
    except Exception as e:
        logger.error(f"on_end err: {e}")


# ─── Очистка ───

async def _cleanup_loop():
    while True:
        try:
            await asyncio.sleep(300)
            now = time.time()
            count = 0
            for f in os.listdir(AUDIO_DIR):
                path = os.path.join(AUDIO_DIR, f)
                try:
                    if now - os.path.getmtime(path) > 600:
                        os.remove(path)
                        count += 1
                except Exception:
                    pass
            if count:
                logger.info(f"Cleanup: removed {count} old files")
        except asyncio.CancelledError:
            break
        except Exception:
            pass


# ─── WebSocket ───

async def ws_loop():
    while True:
        try:
            async with aiohttp.ClientSession() as s:
                ws_url = (
                    f"ws://localhost:8088/ari/events"
                    f"?app={ARI_APP}&api_key={ARI_USER}:{ARI_PASS}"
                )
                async with s.ws_connect(ws_url) as ws:
                    logger.info("ARI connected. Waiting for calls...")
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            ev = json.loads(msg.data)
                            t = ev.get("type", "")
                            if t == "StasisStart":
                                asyncio.create_task(on_start(s, ev))
                            elif t == "RecordingFinished":
                                asyncio.create_task(on_rec_done(s, ev))
                            elif t == "PlaybackFinished":
                                asyncio.create_task(on_play_done(s, ev))
                            elif t == "StasisEnd":
                                asyncio.create_task(on_end(s, ev))
                        elif msg.type in (
                            aiohttp.WSMsgType.ERROR,
                            aiohttp.WSMsgType.CLOSED,
                        ):
                            break
        except Exception as e:
            logger.error(f"ARI err: {e}")
            await asyncio.sleep(5)


# ─── Предзагрузка TTS ───

async def _preload_tts_cache():
    try:
        async with aiohttp.ClientSession() as s:
            phrases = {
                "greeting": settings["greeting"],
                "error": settings["error_message"],
                "thinking": settings["thinking_message"],
                "goodbye": settings["goodbye_message"],
                "budget_exhausted": settings["budget_exhausted_message"],
            }
            for key, text in phrases.items():
                audio = await tts_synthesize(s, text)
                if audio:
                    _tts_cache[key] = audio
                    logger.info(
                        f"Cached TTS: '{key}' ({len(audio)} bytes)"
                    )
                else:
                    logger.warning(f"Failed to cache TTS: '{key}'")
    except Exception as e:
        logger.error(f"TTS preload err: {e}")


# ─── Запуск ───

async def boot(app):
    app["ws"] = asyncio.create_task(ws_loop())
    app["cleanup"] = asyncio.create_task(_cleanup_loop())
    asyncio.create_task(_preload_tts_cache())


async def shutdown(app):
    app["ws"].cancel()
    app["cleanup"].cancel()


def main():
    logger.info("=" * 60)
    logger.info("Voice Bot v11.1")
    logger.info("=" * 60)
    logger.info(f"Record: max {settings['record_max_seconds']}s, "
                f"silence {settings['record_silence_seconds']}s")
    logger.info(f"TTS: {settings['tts_engine']} / {settings['edge_voice']}")
    logger.info(f"STT: Groq {WHISPER_MODEL} @ {WHISPER_URL}")
    logger.info(f"LLM: ModelScope {settings['llm_model']}")
    logger.info(f"Rate: {rate_limiter.min_interval}s interval, "
                f"{rate_limiter.max_per_minute}/min")
    logger.info(f"Budget: {token_budget.daily_limit}/day "
                f"(safety: {token_budget.safety_limit})")
    logger.info(f"Call: max {settings['max_call_duration']}s, "
                f"max turns: {settings['max_conversation_turns']}, "
                f"max empty: {settings['max_empty_stt']}")
    logger.info(f"VAD: {'ON' if settings['vad_enabled'] else 'OFF'}, "
                f"RMS>{settings['vad_rms_threshold']}, "
                f"min_frames={settings['vad_min_active_frames']}, "
                f"min_dur={settings['vad_min_duration']}s")
    logger.info("=" * 60)

    app = web.Application()
    app.router.add_get("/health", api_health)
    app.router.add_get("/settings", api_get_settings)
    app.router.add_post("/settings", api_set_settings)
    app.router.add_get("/calls", api_calls)
    app.on_startup.append(boot)
    app.on_cleanup.append(shutdown)
    web.run_app(app, host="127.0.0.1", port=8099)


if __name__ == "__main__":
    main()
