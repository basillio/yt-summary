"""
Вся «умная» часть приложения: субтитры YouTube, кэш и запросы к Gemini.
Этот файл ничего не знает про веб-сервер, поэтому его легко тестировать отдельно.
"""

import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types
from youtube_transcript_api import YouTubeTranscriptApi

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")  # читаем GEMINI_API_KEY из файла .env

CACHE_DIR = BASE_DIR / "results"          # здесь храним уже скачанные видео
LANGUAGES = ["ru", "uk", "en"]            # в каком порядке искать субтитры
MODELS = ["gemini-flash-latest", "gemini-flash-lite-latest"]  # основная и запасная
RETRIES_PER_MODEL = 3
MAX_HISTORY = 20                          # сколько последних реплик чата помнит ИИ


class AIUnavailable(Exception):
    """Все модели перегружены или упёрлись в лимит бесплатного тарифа."""


# ---------- Время ----------

def format_time(seconds: float) -> str:
    """125.4 -> '02:05', 3725 -> '1:02:05'"""
    seconds = int(seconds)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02}:{s:02}" if h else f"{m:02}:{s:02}"


def parse_time(text) -> int | None:
    """'1:02:05' -> 3725, '05:30' -> 330, '90' -> 90, пусто -> None"""
    text = str(text or "").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) > 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"Не понимаю время «{text}». Пиши так: 05:30 или 1:02:05")
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds


# ---------- Видео и субтитры ----------

def extract_video_id(url: str) -> str:
    """Достаёт 11-символьный ID видео из ссылки любого вида."""
    url = (url or "").strip()
    patterns = [
        r"(?:v=)([\w-]{11})",                  # youtube.com/watch?v=ID
        r"youtu\.be/([\w-]{11})",              # youtu.be/ID
        r"(?:shorts|embed|live)/([\w-]{11})",  # shorts, embed, трансляции
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    if re.fullmatch(r"[\w-]{11}", url):  # передали сам ID
        return url
    raise ValueError("Не нашёл ID видео в ссылке. Проверь, что это ссылка на YouTube.")


def _cache_file(video_id: str) -> Path:
    return CACHE_DIR / f"{video_id}.json"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def fetch_title(video_id: str) -> str | None:
    """Название видео через открытый сервис YouTube oEmbed (ключ не нужен)."""
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    url = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(video_url, safe="")
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.loads(r.read().decode("utf-8")).get("title")
    except Exception:
        return None  # название — не главное, без него тоже работаем


def save_video(data: dict, touch: bool = False) -> None:
    """Сохраняет видео в results/. touch=True — отметить время последней активности."""
    if touch:
        data["updated"] = _now()
    CACHE_DIR.mkdir(exist_ok=True)
    _cache_file(data["video_id"]).write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def load_video(video_id: str) -> dict:
    """Берёт видео из сохранённых, а если его там нет — скачивает субтитры с YouTube."""
    path = _cache_file(video_id)
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        # Файлы из прошлой версии: дописываем недостающие поля
        if "title" not in data or "chat" not in data:
            data.setdefault("chat", [])
            data.setdefault("created", _now())
            data.setdefault("updated", data["created"])
            if "title" not in data:
                data["title"] = fetch_title(video_id)
            save_video(data)
        return data

    fetched = YouTubeTranscriptApi().fetch(video_id, languages=LANGUAGES)
    data = {
        "video_id": video_id,
        "title": fetch_title(video_id),
        "language": fetched.language_code,
        "created": _now(),
        "updated": _now(),
        "segments": [
            {"start": s.start, "duration": s.duration, "text": s.text} for s in fetched
        ],
        "summary": None,
        "chat": [],  # диалог: [{role, text, label, time}]
    }
    save_video(data)
    return data


def list_videos() -> list[dict]:
    """Список всех разобранных видео, свежие сверху."""
    items = []
    if not CACHE_DIR.exists():
        return items
    for path in CACHE_DIR.glob("*.json"):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue  # битый файл просто пропускаем
        if "video_id" not in d:
            continue
        file_time = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
        items.append({
            "video_id": d["video_id"],
            "title": d.get("title") or d["video_id"],
            "updated": d.get("updated") or file_time,
            "questions": sum(1 for m in d.get("chat", []) if m.get("role") == "user"),
        })
    items.sort(key=lambda x: x["updated"], reverse=True)
    return items


def delete_video(video_id: str) -> None:
    _cache_file(video_id).unlink(missing_ok=True)


def transcript_text(segments: list, start=None, end=None) -> str:
    """Склеивает субтитры в текст с таймкодами. start/end — границы в секундах."""
    lines = []
    for s in segments:
        if start is not None and s["start"] + s["duration"] < start:
            continue
        if end is not None and s["start"] > end:
            break
        lines.append(f"[{format_time(s['start'])}] {s['text']}")
    return "\n".join(lines)


# ---------- Gemini ----------

_client = None


def _get_client():
    """Создаём клиента один раз и дальше переиспользуем."""
    global _client
    if _client is None:
        if not os.getenv("GEMINI_API_KEY"):
            raise RuntimeError("Не найден GEMINI_API_KEY. Проверь файл .env рядом с app.py")
        _client = genai.Client()
    return _client


def ask_ai(system: str, contents: list) -> tuple[str, str]:
    """Отправляет запрос в Gemini. При перегрузке повторяет и переключает модель.
    Возвращает (текст ответа, имя модели)."""
    client = _get_client()
    config = types.GenerateContentConfig(
        system_instruction=system,
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    for model in MODELS:
        for attempt in range(1, RETRIES_PER_MODEL + 1):
            try:
                response = client.models.generate_content(
                    model=model, contents=contents, config=config
                )
                return response.text or "", model
            except errors.APIError as e:
                if e.code not in (429, 503):  # 503 — перегрузка, 429 — лимит
                    raise
                wait = 5 * attempt
                print(f"{model}: ошибка {e.code}, попытка {attempt}, жду {wait} с")
                time.sleep(wait)
    raise AIUnavailable("Модели Gemini сейчас перегружены. Попробуй ещё раз через минуту.")


def _user(text: str) -> dict:
    return {"role": "user", "parts": [{"text": text}]}


def make_summary(data: dict) -> tuple[str, str]:
    system = "Ты помогаешь быстро понять содержание YouTube-видео. Отвечай на русском языке."
    prompt = (
        "Ниже транскрипция YouTube-видео с таймкодами. Сделай:\n"
        "1. Краткую выжимку в 3–5 предложений.\n"
        "2. Список ключевых мыслей, у каждой таймкод в квадратных скобках, например [05:30].\n"
        "3. Вывод: кому и чем полезно это видео.\n\n"
        f"<transcript>\n{transcript_text(data['segments'])}\n</transcript>"
    )
    return ask_ai(system, [_user(prompt)])


def fragment_label(start=None, end=None) -> str:
    if start is None and end is None:
        return ""
    end_text = format_time(end) if end is not None else "конец"
    return f"Фрагмент {format_time(start or 0)}–{end_text}"


def answer_question(data: dict, question: str, start=None, end=None):
    """Отвечает на вопрос о видео и сохраняет вопрос и ответ в диалог.
    start/end (в секундах) — если вопрос про конкретный фрагмент.
    Возвращает (ответ, модель, подпись фрагмента)."""
    system = (
        "Ты отвечаешь на вопросы о YouTube-видео по его транскрипции.\n"
        "Правила:\n"
        "- Отвечай на русском языке, по существу.\n"
        "- Опирайся на транскрипцию и указывай таймкоды в квадратных скобках, например [05:30].\n"
        "- Если в видео ответа нет, прямо скажи об этом. Общие знания добавляй, "
        "только явно пометив, что этого в видео нет.\n"
        "- Субтитры могут быть автоматическими, с ошибками распознавания: "
        "понимай слова по смыслу.\n\n"
        f"<transcript>\n{transcript_text(data['segments'])}\n</transcript>"
    )

    # ИИ сам ничего не помнит, поэтому каждый раз отправляем ему прошлый диалог
    contents = []
    for msg in data["chat"][-MAX_HISTORY:]:
        text = msg["text"]
        if msg.get("label"):
            text = f"[{msg['label']}] {text}"
        contents.append({"role": msg["role"], "parts": [{"text": text}]})

    label = fragment_label(start, end)
    prompt = question
    if label:
        fragment = transcript_text(data["segments"], start, end)
        if not fragment:
            raise ValueError("В этом промежутке нет субтитров. Проверь границы фрагмента.")
        prompt = f"Вопрос касается фрагмента:\n<fragment>\n{fragment}\n</fragment>\n\n{question}"

    contents.append(_user(prompt))
    answer, model = ask_ai(system, contents)

    # Сохраняем обмен репликами на диск
    now = _now()
    data["chat"].append({"role": "user", "text": question, "label": label, "time": now})
    data["chat"].append({"role": "model", "text": answer, "time": now, "model": model})
    save_video(data, touch=True)
    return answer, model, label
