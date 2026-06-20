@echo off
REM Instagram Cookie Refresh — run by Windows Task Scheduler every 4 hours

cd /d "C:\Users\skang\telebot"
call venv\Scripts\activate.bat
set PYTHONIOENCODING=utf-8
python cookie_manager.py
