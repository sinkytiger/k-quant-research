@echo off
REM K-Quant 월간: 유니버스 월초 스냅샷 재구성(API 호출 없음) + 무효 시총 파일 정리 + 테스트
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"
set PYTHONWARNINGS=ignore
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMM"') do set MONTH=%%i
if not exist logs mkdir logs
set "LOG=logs\monthly_%MONTH%.log"
"%PY%" scripts\collect_universe.py --membership --status >> "%LOG%" 2>&1
"%PY%" scripts\collect_marketcap.py --update --status >> "%LOG%" 2>&1
"%PY%" scripts\collect_dividends.py >> "%LOG%" 2>&1
"%PY%" -m pytest -q -p no:warnings >> "%LOG%" 2>&1
echo [pytest] exit %errorlevel% >> "%LOG%"
endlocal
