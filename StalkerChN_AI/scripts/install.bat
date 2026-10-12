@echo off
REM ============================================================
REM  StalkerChN_AI - установка сервера ИИ (Windows, Python 3.10+)
REM  Двойной клик по этому файлу, либо запуск из cmd.
REM ============================================================
setlocal
cd /d "%~dp0.."

where python >nul 2>nul
if errorlevel 1 (
  echo [ОШИБКА] Python не найден. Установите Python 3.10+ с python.org
  echo и поставьте галочку "Add Python to PATH".
  pause & exit /b 1
)

if not exist .venv (
  echo [*] Создаю виртуальное окружение .venv ...
  python -m venv .venv
)

call .venv\Scripts\activate.bat

echo [*] Ставлю базовые зависимости (быстрые)...
pip install --upgrade pip -q
pip install -q fastapi "uvicorn[standard]" websockets pydantic httpx numpy

echo.
echo Готово! Базовый режим (тест без реального голоса/LLM) уже работает:
echo   - STT-заглушка принимает текст через POST /api/text
echo   - LLM backend=mock отвечает шаблонами
echo.
echo Для полноценной работы выберите бэкенды в config\settings.ini и установите:
echo   Локальный Whisper (рекомендуется): pip install faster-whisper
echo   Лёгкий русский Vosk:              pip install vosk
echo   Локальный LLM:                    скачайте ollama.com, затем: ollama pull qwen2.5:7b-instruct
echo   Голоса NPC (TTS):                 pip install edge-tts
echo.
pause
