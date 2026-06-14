@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
call venv\Scripts\activate.bat
python -X utf8=1 bot.py