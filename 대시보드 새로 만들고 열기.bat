@echo off
REM 지금 데이터로 대시보드를 다시 만들고 연다 (약 1분, API 호출 없음).
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"
set PYTHONWARNINGS=ignore
echo 대시보드를 새로 만드는 중입니다...
"%PY%" scripts\build_dashboard.py
if errorlevel 1 (
  echo 만들기 실패. 위 메시지를 확인하세요.
  pause
  exit /b 1
)
start "" "%~dp0outputs\dashboard.html"
endlocal
