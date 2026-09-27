# 가상환경 파이썬으로 도구 실행.  예) .\run.ps1 lab.py --note "첫 실행"
Set-Location $PSScriptRoot
$env:PYTHONUTF8 = "1"
& (Join-Path $PSScriptRoot ".venv\Scripts\python.exe") @args
