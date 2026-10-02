@echo off
REM K-Quant 일일 배치: KRX 스냅샷 증분 -> KIS 수급 증분 -> KIS 뉴스 증분 -> 페이퍼 NAV -> 대시보드. 월 1회 run_monthly.
REM KRX 는 D일 데이터를 D+1 오전 8시 전후에 준다 -> 08:40 실행 권장.
REM PC 가 꺼져 있던 기간은 "마지막 저장일 이후"부터 받으므로 자동으로 메워진다.
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"
set PYTHONWARNINGS=ignore
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set TODAY=%%i
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMM"') do set MONTH=%%i
if not exist logs mkdir logs
set "LOG=logs\daily_%TODAY%.log"

echo ==== %date% %time% daily start >> "%LOG%"
"%PY%" scripts\collect_universe.py --update --markets stk ksq >> "%LOG%" 2>&1
echo [universe] exit %errorlevel% >> "%LOG%"
"%PY%" scripts\collect_flows.py --update >> "%LOG%" 2>&1
echo [flows] exit %errorlevel% >> "%LOG%"
"%PY%" scripts\collect_news.py --update >> "%LOG%" 2>&1
echo [news] exit %errorlevel% >> "%LOG%"
"%PY%" scripts\collect_market_extra.py >> "%LOG%" 2>&1
echo [market_extra] exit %errorlevel% >> "%LOG%"
"%PY%" scripts\paper_track.py --update --report >> "%LOG%" 2>&1
echo [paper] exit %errorlevel% >> "%LOG%"
"%PY%" scripts\build_dashboard.py >> "%LOG%" 2>&1
echo [dashboard] exit %errorlevel% >> "%LOG%"

if not exist "logs\monthly_%MONTH%.stamp" (
  call "%~dp0run_monthly.bat"
  echo done > "logs\monthly_%MONTH%.stamp"
)
echo ==== %date% %time% daily end >> "%LOG%"
endlocal
