"""
Веб-сервер. Запуск:  python app.py  →  открыть http://127.0.0.1:5000
Сервер только принимает запросы от страницы и зовёт функции из core.py.
"""

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


if __name__ == "__main__":
    print("Открой в браузере: http://127.0.0.1:5000")
    app.run(debug=True)
