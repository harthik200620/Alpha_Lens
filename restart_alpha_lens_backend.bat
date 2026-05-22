@echo off
cd /d "%~dp0"
".alpha-venv\Scripts\python.exe" -u backend\app.py > restart_server.log 2> restart_server_err.log
