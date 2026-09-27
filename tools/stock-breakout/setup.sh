#!/usr/bin/env bash
# 주식 도구 설치 (맥·리눅스). 이 폴더 .venv 에만 설치합니다.  실행: bash setup.sh
set -e
cd "$(dirname "$0")"
PY=$(command -v python3 || command -v python) || { echo "Python 3.11 이상을 먼저 설치하세요"; exit 1; }
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip -q
.venv/bin/python -m pip install -r requirements.txt -q
[ -f autotrade_config.json ] || cp autotrade_config.example.json autotrade_config.json
PYTHONUTF8=1 .venv/bin/python doctor.py
echo "완료. 앞으로는  .venv/bin/python lab.py --note \"메모\"  처럼 실행하세요."
