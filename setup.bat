@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [JP 숏폼 작업실] 설치를 시작합니다.
set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY ( where python >nul 2>nul && set PY=python )
if not defined PY (
  echo Python을 찾을 수 없습니다. https://www.python.org/downloads/ 에서 Python 3.11 이상을 설치하고
  echo 설치 화면의 "Add python.exe to PATH"를 체크한 뒤 다시 실행하세요.
  pause
  exit /b 1
)
%PY% -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)"
if errorlevel 1 ( echo Python 3.10 이상이 필요합니다. & pause & exit /b 1 )
if not exist .venv ( %PY% -m venv .venv )
if errorlevel 1 ( echo 가상환경을 만들지 못했습니다. & pause & exit /b 1 )
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
if errorlevel 1 ( echo 패키지 설치에 실패했습니다. 인터넷 연결을 확인하고 다시 실행하세요. & pause & exit /b 1 )
echo.
echo 설치가 끝났습니다. start.bat 을 더블클릭해 실행하세요.
echo (선택) 음성 인식 보조 자막 정렬을 쓰려면 install_whisper.bat 을 실행하세요.
pause
