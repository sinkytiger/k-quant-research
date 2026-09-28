# K-Quant 작업 스케줄러 등록 (현재 사용자, 관리자 권한 불필요)
#   KQuant-Daily : 월~토 08:40  run_daily.bat   (KRX 는 D일 데이터를 D+1 08시 전후 제공)
#   KQuant-Weekly: 토   09:30  run_weekly.bat
# StartWhenAvailable: 예약 시각에 PC 가 꺼져 있었으면 켜진 뒤 바로 실행 (schtasks.exe 로는 못 켠다)
# 해제: Unregister-ScheduledTask -TaskName KQuant-Daily,KQuant-Weekly -Confirm:$false
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 6) -MultipleInstances IgnoreNew

$daily = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday,Saturday -At 08:40
$dailyAction = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"`"$root\run_daily.bat`"`"" -WorkingDirectory $root
Register-ScheduledTask -TaskName "KQuant-Daily" -Trigger $daily -Action $dailyAction -Settings $settings `
    -Description "K-Quant: KRX/KIS 증분 수집 + 페이퍼 NAV" -Force | Out-Null

$weekly = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At 09:30
$weeklyAction = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"`"$root\run_weekly.bat`"`"" -WorkingDirectory $root
Register-ScheduledTask -TaskName "KQuant-Weekly" -Trigger $weekly -Action $weeklyAction -Settings $settings `
    -Description "K-Quant: 데이터 상태 + 페이퍼 게이트 리포트" -Force | Out-Null

Get-ScheduledTask -TaskName "KQuant-*" | Select-Object TaskName, State, @{n="Next";e={(Get-ScheduledTaskInfo $_).NextRunTime}} | Format-Table -AutoSize
