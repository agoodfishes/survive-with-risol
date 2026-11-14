@echo off
cd /d "%~dp0"
call venv\Scripts\activate.bat
py bot.py
pause