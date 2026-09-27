# 주식 도구 설치 (윈도우). 이 폴더 안 .venv 에만 설치하므로 다른 파이썬 프로그램과 충돌하지 않습니다.
# 실행: 이 폴더에서  powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = Get-Command py -ErrorAction SilentlyContinue
if ($py) { $base = "py"; $baseArgs = @("-3") } else {
    $py = Get-Command python -ErrorAction SilentlyContinue
    if (-not $py) { Write-Host "Python 3.11 이상을 먼저 설치하세요 (python.org, 설치 화면에서 'Add python.exe to PATH' 체크)"; exit 1 }
    $base = "python"; $baseArgs = @()
}
if (-not (Test-Path ".venv")) {
    Write-Host "가상환경 만드는 중 (.venv)..."
    & $base @baseArgs -m venv .venv
}
$vpy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $vpy -m pip install --upgrade pip --quiet
Write-Host "라이브러리 설치 중..."
& $vpy -m pip install -r requirements.txt --quiet
if (-not (Test-Path "autotrade_config.json")) { Copy-Item "autotrade_config.example.json" "autotrade_config.json"; Write-Host "autotrade_config.json 생성 (모의매매 paper 모드)" }
$env:PYTHONUTF8 = "1"
& $vpy doctor.py
Write-Host ""
Write-Host "완료. 앞으로는  .\run.ps1 lab.py --note `"메모`"  처럼 실행하세요."
