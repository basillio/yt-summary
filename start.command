#!/bin/bash
# Конспектор — запуск на macOS. Двойной клик по файлу в Finder.
# Всё ставится в папку .venv рядом со скриптом, систему не трогаем.

cd "$(dirname "$0")" || exit 1

echo "=========================================="
echo "  Конспектор — выжимки YouTube-видео"
echo "=========================================="
echo

pause_exit() {
    echo
    read -r -p "Нажми Enter, чтобы закрыть окно..." _
    exit "${1:-1}"
}

# Ищем Python 3.10 или новее. Сначала явные пути (Homebrew, python.org),
# системный python3 в конце: на чистом Mac он старый (3.9).
find_python() {
    for cmd in /opt/homebrew/bin/python3 /usr/local/bin/python3 \
               /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
               python3.13 python3.12 python3.11 python3.10 python3; do
        if command -v "$cmd" >/dev/null 2>&1 && \
           "$cmd" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
            echo "$cmd"
            return 0
        fi
    done
    return 1
}

# ---------- 1. Python ----------
if ! PY=$(find_python); then
    BREW=$(command -v brew || ls /opt/homebrew/bin/brew /usr/local/bin/brew 2>/dev/null | head -n 1)
    if [ -n "$BREW" ]; then
        echo "[1/4] Нужен Python 3.10+. Устанавливаю через Homebrew..."
        "$BREW" install python || pause_exit
        PY=$(find_python) || { echo "Python не нашёлся после установки."; pause_exit; }
    else
        echo "[1/4] Нужен Python 3.10 или новее."
        echo "Сейчас откроется python.org: скачай установщик для macOS и установи его."
        echo "Потом запусти start.command ещё раз."
        open "https://www.python.org/downloads/macos/"
        pause_exit
    fi
fi
echo "[1/4] Python: $("$PY" --version)"

# ---------- 2. Окружение и библиотеки ----------
if [ ! -x ".venv/bin/python" ]; then
    echo "[2/4] Создаю окружение .venv..."
    "$PY" -m venv .venv || pause_exit
fi
VPY=".venv/bin/python"
if ! "$VPY" -c "import flask, google.genai, youtube_transcript_api, dotenv, requests" >/dev/null 2>&1; then
    echo "[2/4] Устанавливаю библиотеки, подожди..."
    "$VPY" -m pip install --disable-pip-version-check -r requirements.txt || pause_exit
fi
echo "[2/4] Библиотеки установлены"

# ---------- 3. API-ключ ----------
if [ ! -f ".env" ]; then
    echo
    echo "[3/4] Нужен бесплатный ключ Gemini."
    echo "Сейчас откроется страница: войди через Google, нажми «Create API key», скопируй ключ."
    open "https://aistudio.google.com/apikey"
    echo
    read -r -p "Вставь ключ сюда (Cmd+V) и нажми Enter: " APIKEY
    APIKEY=$(printf '%s' "$APIKEY" | tr -d '[:space:]')
    if [ -z "$APIKEY" ]; then
        echo "Ключ пустой. Запусти start.command ещё раз."
        pause_exit
    fi
    printf 'GEMINI_API_KEY=%s\n' "$APIKEY" > .env
    echo "Ключ сохранён в .env"
fi
echo "[3/4] Ключ найден"

# ---------- 4. Сервер (браузер откроется сам) ----------
echo "[4/4] Запускаю сервер..."
echo
echo "  Не закрывай это окно, пока пользуешься приложением."
echo "  Остановить: Ctrl+C или закрыть окно."
echo
"$VPY" app.py

echo
echo "Сервер остановлен."
pause_exit 0
