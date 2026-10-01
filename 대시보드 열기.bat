@echo off
REM 대시보드를 기본 브라우저로 연다 (매일 08:40 배치가 새로 만든 파일).
chcp 65001 >nul
cd /d "%~dp0"
if not exist "outputs\dashboard.html" (
  echo 대시보드 파일이 아직 없습니다. "대시보드 새로 만들고 열기"를 실행하세요.
  pause
  exit /b 1
)
start "" "%~dp0outputs\dashboard.html"
