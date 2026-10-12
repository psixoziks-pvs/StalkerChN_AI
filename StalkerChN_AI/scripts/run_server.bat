@echo off
REM Запуск сервера ИИ StalkerChN_AI (порт 8765, см. config\settings.ini)
setlocal
cd /d "%~dp0.."
if exist .venv\Scripts\activate.bat ( call .venv\Scripts\activate.bat )
python scripts\server\main.py
pause
