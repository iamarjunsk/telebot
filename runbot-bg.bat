@echo off
cd /d "%~dp0"
pm2 kill 2>nul
ping -n 2 127.0.0.1 >nul
pm2 start bot_launcher.py --name telebot --interpreter python
pm2 save