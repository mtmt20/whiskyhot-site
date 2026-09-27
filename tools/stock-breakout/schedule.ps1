# 모의매매 자동 실행을 윈도우 작업 스케줄러에 등록. 작업 이름은 모두 StockLab_ 로 시작합니다.
# 등록:  powershell -ExecutionPolicy Bypass -File schedule.ps1
# 해제:  powershell -ExecutionPolicy Bypass -File schedule.ps1 -Remove
param([switch]$Remove)
$names = "StockLab_plan", "StockLab_report", "StockLab_enter", "StockLab_monitor", "StockLab_close"
if ($Remove) {
    foreach ($n in $names) { schtasks /delete /tn $n /f 2>$null | Out-Null }
    Write-Host "StockLab_ 작업을 모두 해제했습니다."; exit 0
}
$dir = $PSScriptRoot
$vpy = Join-Path $dir ".venv\Scripts\python.exe"
if (-not (Test-Path $vpy)) { Write-Host "먼저 setup.ps1 을 실행하세요."; exit 1 }
$days = "MON,TUE,WED,THU,FRI"
function Add($name, $cmd, $time, $extra, $script = "autotrade.py") {
    $tr = "cmd /c cd /d `"$dir`" && `"$vpy`" -X utf8 $script $cmd >> autotrade_state\task.log 2>&1"
    $a = @("/create", "/f", "/tn", $name, "/tr", $tr, "/sc", "weekly", "/d", $days, "/st", $time) + $extra
    schtasks @a | Out-Null
    Write-Host "등록: $name ($time)"
}
New-Item -ItemType Directory -Force -Path (Join-Path $dir "autotrade_state") | Out-Null
Add "StockLab_plan" "plan" "16:10" @()
Add "StockLab_report" "" "16:30" @() "daily_report.py"
Add "StockLab_enter" "enter" "09:32" @()
Add "StockLab_monitor" "monitor" "09:35" @("/ri", "5", "/du", "06:00")
Add "StockLab_close" "close" "15:21" @()
Write-Host "매일 16:30 보고서: reports\latest.html (텔레그램 설정 시 휴대폰으로 전송)"
Write-Host "장중에는 컴퓨터가 켜져 있고 절전 모드가 꺼져 있어야 합니다. 로그: autotrade_state\task.log"
