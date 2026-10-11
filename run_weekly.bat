@echo off
REM K-Quant 주간 점검: 데이터 상태 + 페이퍼 게이트 리포트 + DART 재무 갱신 + 데이터 백업(OneDrive\KQuantBackup)
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"
set PYTHONWARNINGS=ignore
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set TODAY=%%i
if not exist logs mkdir logs
set "LOG=logs\weekly_%TODAY%.log"

REM 따라잡기: 토요일 09:30 말고도 로그온·절전 해제 때 불린다. 가장 최근 토요일 09:30 이후에 이미 돌았으면(그 뒤 날짜의 weekly 로그가 있으면) 끝낸다.
REM 손으로 다시 돌릴 때는: run_weekly.bat force
if /i not "%~1"=="force" (
  powershell -NoProfile -Command "$n=Get-Date; $s=$n.Date.AddDays(-((([int]$n.DayOfWeek)+1)%%7)); if ($n -lt $s.AddHours(9.5)) { $s=$s.AddDays(-7) }; $done=Get-ChildItem logs -Filter 'weekly_*.log' | Where-Object { [datetime]::ParseExact($_.BaseName.Substring(7),'yyyyMMdd',$null) -ge $s.Date }; if ($done) { exit 1 }"
  if errorlevel 1 exit /b 0
)
"%PY%" scripts\collect_universe.py --status >> "%LOG%" 2>&1
"%PY%" scripts\collect_flows.py --status >> "%LOG%" 2>&1
"%PY%" scripts\collect_marketcap.py --status >> "%LOG%" 2>&1
"%PY%" scripts\paper_track.py --report >> "%LOG%" 2>&1
"%PY%" scripts\collect_fin.py --update >> "%LOG%" 2>&1
echo [fin] exit %errorlevel% >> "%LOG%"
"%PY%" scripts\backup_data.py >> "%LOG%" 2>&1
echo [backup] exit %errorlevel% >> "%LOG%"
endlocal
