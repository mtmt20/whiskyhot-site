#!/usr/bin/env python3
"""설치 점검: 라이브러리, 키, 데이터 접속을 한 번에 확인하고 해결 방법을 알려준다. 주문은 내지 않는다."""
from __future__ import annotations

import importlib
import os
import sys
import tempfile

OK, NG, WARN = "[정상]", "[문제]", "[선택]"
rows = []


def check(name, fn, optional=False):
    try:
        msg = fn()
        rows.append((OK, name, msg or ""))
    except Exception as ex:  # noqa: BLE001
        msg = str(ex) if isinstance(ex, RuntimeError) else f"{type(ex).__name__}: {str(ex)[:120]}"
        rows.append((WARN if optional else NG, name, msg))


def py_version():
    v = sys.version_info
    if v < (3, 10):
        raise RuntimeError(f"Python {v.major}.{v.minor} → 3.11 이상 권장")
    return f"Python {v.major}.{v.minor}.{v.micro}"


def libs():
    missing = []
    for m in ("pandas", "numpy", "yfinance", "pykrx", "matplotlib", "requests"):
        try:
            importlib.import_module(m)
        except Exception:
            missing.append(m)
    if missing:
        raise RuntimeError("설치 안 됨: " + ", ".join(missing) + " → pip install -r requirements.txt")
    return "필수 라이브러리 모두 설치됨"


def writable():
    d = os.path.dirname(os.path.abspath(__file__))
    with tempfile.NamedTemporaryFile(dir=d, delete=True):
        pass
    return "이 폴더에 결과 파일 저장 가능"


def yahoo():
    import yfinance as yf
    h = yf.download("005930.KS", period="5d", progress=False, auto_adjust=True)
    if h is None or h.empty:
        raise RuntimeError("삼성전자 시세를 못 받음. 인터넷·방화벽 확인")
    return f"야후 시세 정상 (삼성전자 최근 {len(h)}일)"


def krx():
    if not (os.environ.get("KRX_ID") and os.environ.get("KRX_PW")):
        raise RuntimeError("KRX_ID, KRX_PW 미설정 → 수급 지표(--flows) 사용 불가. data.krx.co.kr 무료 가입 후 설정")
    from pykrx import stock
    from datetime import date, timedelta
    d = (date.today() - timedelta(days=10)).strftime("%Y%m%d")
    t = stock.get_market_trading_value_by_date(d, date.today().strftime("%Y%m%d"), "005930")
    if t is None or t.empty:
        raise RuntimeError("KRX 수급 조회 실패. 아이디·비밀번호 확인")
    return "KRX 투자자별 수급 정상"


def dart():
    key = os.environ.get("DART_API_KEY")
    if not key:
        raise RuntimeError("DART_API_KEY 미설정 → story.py, intent.py 사용 불가. opendart.fss.or.kr 무료 발급")
    import requests
    r = requests.get("https://opendart.fss.or.kr/api/list.json",
                     params={"crtfc_key": key, "page_count": 1}, timeout=15).json()
    if r.get("status") not in ("000", "013"):
        raise RuntimeError(f"DART 응답: {r.get('message')}")
    return "DART 공시 조회 정상"


def kis():
    need = [k for k in ("KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT") if not os.environ.get(k)]
    if need:
        raise RuntimeError("미설정: " + ", ".join(need) + " → 증권사 연결 전까지는 paper 모드로만 사용")
    return "한국투자증권 키 설정됨 (연결 확인은 모드를 kis_mock 으로 바꾼 뒤 autotrade.py status)"


def config():
    import json
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "autotrade_config.json")
    if not os.path.exists(p):
        raise RuntimeError("autotrade_config.json 없음 → autotrade_config.example.json 을 복사")
    mode = json.load(open(p, encoding="utf-8")).get("mode", "paper")
    return f"자동매매 모드: {mode}" + ("  (실전 모드입니다. 의도한 것인지 확인하세요)" if mode == "kis_live" else "")


check("파이썬 버전", py_version)
check("라이브러리", libs)
check("저장 권한", writable)
check("시세 (야후)", yahoo)
check("수급 (KRX)", krx, optional=True)
check("공시 (DART)", dart, optional=True)
check("증권사 (한국투자증권)", kis, optional=True)
check("자동매매 설정", config, optional=True)

print("\n설치 점검 결과")
print("-" * 70)
for s, n, m in rows:
    print(f"{s} {n:<16} {m}")
bad = [r for r in rows if r[0] == NG]
print("-" * 70)
print("필수 항목 문제 없음. 바로 사용할 수 있습니다." if not bad else f"필수 항목 {len(bad)}개를 먼저 해결하세요.")
sys.exit(1 if bad else 0)
