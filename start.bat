@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo 먼저 setup.bat 을 실행해 설치하세요.
  pause
  exit /b 1
)
.venv\Scripts\python run.py
pause
