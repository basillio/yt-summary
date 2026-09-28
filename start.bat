@echo off
title Konspektor
rem Go to the folder where this .bat file is located
cd /d "%~dp0"

echo ==========================================
echo   Konspektor - YouTube summary
echo ==========================================
echo.

rem ---------- 1. Find Python ----------
set "PY="
python --version >nul 2>nul && set "PY=python"
if not defined PY py --version >nul 2>nul && set "PY=py"

if not defined PY (
    echo [1/4] Python not found. Trying to install it via winget...
    where winget >nul 2>nul
    if errorlevel 1 (
        echo Could not install automatically.
        echo Download Python from the page that will open now.
        echo IMPORTANT: check "Add python.exe to PATH" during install.
        start "" https://www.python.org/downloads/
        pause
        exit /b 1
    )
    winget install -e --id Python.Python.3.13 --accept-package-agreements --accept-source-agreements
    echo.
    echo Python installed. Close this window and run start.bat again.
    pause
    exit /b 0
)
echo [1/4] Python found: %PY%

rem ---------- 2. Install libraries (only if missing) ----------
%PY% -c "import flask, google.genai, youtube_transcript_api, dotenv, requests" >nul 2>nul
if errorlevel 1 (
    echo [2/4] Installing libraries, please wait...
    %PY% -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 (
        echo.
        echo ERROR: could not install libraries. See the message above.
        pause
        exit /b 1
    )
)
echo [2/4] Libraries are installed

rem ---------- 3. API key ----------
if not exist ".env" (
    echo.
    echo [3/4] Gemini API key is needed - it is free.
    echo A page will open: sign in with Google, click "Create API key", copy it.
    start "" https://aistudio.google.com/apikey
    echo.
    set /p "APIKEY=Paste the key here and press Enter: "
    call :savekey
)
echo [3/4] API key file .env found

rem ---------- 4. Start server (app.py opens the browser itself) ----------
echo [4/4] Starting server...
echo.
echo   The browser will open automatically: http://127.0.0.1:5055
echo   Keep this window open while using the app.
echo   To stop: close this window or press Ctrl+C.
echo.
%PY% app.py

echo.
echo Server stopped.
pause
exit /b 0

:savekey
if "%APIKEY%"=="" (
    echo The key is empty. Run start.bat again and paste the key.
    pause
    exit
)
> ".env" echo GEMINI_API_KEY=%APIKEY%
echo Key saved to .env
exit /b 0
