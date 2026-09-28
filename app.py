"""
Веб-сервер. Запуск:  python app.py  — браузер откроется сам (http://127.0.0.1:5055)
Сервер только принимает запросы от страницы и зовёт функции из core.py.
"""

import os
import threading
import webbrowser

from flask import Flask, jsonify, render_template, request
from google.genai import errors as genai_errors
from youtube_transcript_api._errors import CouldNotRetrieveTranscript

import core

app = Flask(__name__)


# ---------- Ошибки: превращаем исключения в понятный JSON для страницы ----------

@app.errorhandler(ValueError)
def bad_input(e):
    return jsonify(error=str(e)), 400


@app.errorhandler(CouldNotRetrieveTranscript)
def no_transcript(e):
    return jsonify(error="У этого видео нет субтитров на русском, украинском или "
                         "английском, либо YouTube не отдал их. Попробуй другое видео."), 404


@app.errorhandler(core.AIUnavailable)
def ai_busy(e):
    return jsonify(error=str(e)), 503


@app.errorhandler(genai_errors.APIError)
def ai_error(e):
    return jsonify(error=f"Gemini вернул ошибку {e.code}: {e.message}"), 502


@app.errorhandler(RuntimeError)
def config_error(e):
    return jsonify(error=str(e)), 500


# ---------- Страница ----------

@app.get("/")
def index():
    return render_template("index.html")


# ---------- API ----------

def _video_from(body: dict) -> dict:
    return core.load_video(core.extract_video_id(body.get("video_id") or body.get("url", "")))


@app.post("/api/video")
def video():
    """Ссылка или ID → субтитры, выжимка и сохранённый диалог."""
    data = _video_from(request.get_json(silent=True) or {})
    return jsonify(
        video_id=data["video_id"],
        title=data.get("title"),
        language=data["language"],
        segments=data["segments"],
        summary=data["summary"],
        chat=data["chat"],
    )


@app.get("/api/history")
def history():
    """Список всех разобранных видео."""
    return jsonify(videos=core.list_videos())


@app.post("/api/video/delete")
def delete_video():
    body = request.get_json(silent=True) or {}
    core.delete_video(core.extract_video_id(body.get("video_id", "")))
    return jsonify(ok=True)


@app.post("/api/summary")
def summary():
    """Делает выжимку. force=true — пересоздать, даже если она уже есть."""
    body = request.get_json(silent=True) or {}
    data = _video_from(body)
    model = None
    if body.get("force") or not data["summary"]:
        data["summary"], model = core.make_summary(data)
        core.save_video(data, touch=True)
    return jsonify(summary=data["summary"], model=model)


@app.post("/api/ask")
def ask():
    """Вопрос про всё видео или про фрагмент (start/end в формате 05:30)."""
    body = request.get_json(silent=True) or {}
    question = str(body.get("question", "")).strip()
    if not question:
        raise ValueError("Вопрос пустой.")

    start = core.parse_time(body.get("start"))
    end = core.parse_time(body.get("end"))
    if start is not None and end is not None and end <= start:
        raise ValueError("Конец фрагмента должен быть позже начала.")

    data = _video_from(body)
    answer, model, label = core.answer_question(data, question, start, end)
    return jsonify(answer=answer, model=model, label=label)


@app.post("/api/chat/clear")
def clear_chat():
    data = _video_from(request.get_json(silent=True) or {})
    data["chat"] = []
    core.save_video(data, touch=True)
    return jsonify(ok=True)


# Порт 5000 на macOS занят AirPlay, поэтому берём 5055.
# Поменять можно строкой PORT=... в файле .env
PORT = int(os.getenv("PORT", "5055"))

if __name__ == "__main__":
    url = f"http://127.0.0.1:{PORT}"
    print(f"Приложение: {url}")
    # В debug-режиме Flask запускает программу дважды (второй раз — для автоперезагрузки).
    # Браузер открываем только в первом процессе и через 1,5 с, когда сервер уже поднялся.
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        threading.Timer(1.5, webbrowser.open, args=[url]).start()
    app.run(host="127.0.0.1", port=PORT, debug=True)
