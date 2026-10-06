@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 음성 인식 보조 자막 정렬(faster-whisper)을 설치합니다. 프로그램 약 100MB.
echo 모델(기본 small 약 480MB)은 처음 사용할 때 Hugging Face에서 내려받습니다.
echo 음성 파일은 외부로 보내지 않고 이 PC에서만 처리합니다.
.venv\Scripts\python -m pip install -r requirements-optional.txt
pause
