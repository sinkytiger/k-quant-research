@echo off
REM K-Quant 주간 점검: 데이터 상태 + 페이퍼 게이트 리포트
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"
set PYTHONWARNINGS=ignore
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set TODAY=%%i
if not exist logs mkdir logs
set "LOG=logs\weekly_%TODAY%.log"
"%PY%" scripts\collect_universe.py --status >> "%LOG%" 2>&1
"%PY%" scripts\collect_flows.py --status >> "%LOG%" 2>&1
"%PY%" scripts\collect_marketcap.py --status >> "%LOG%" 2>&1
"%PY%" scripts\paper_track.py --report >> "%LOG%" 2>&1
endlocal
