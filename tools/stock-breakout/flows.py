"""
한국 투자자별 수급(외국인·기관 순매수 금액)과 공매도 잔고 비중 로더.
pykrx로 KRX 데이터를 받아 CSV로 캐시한다. 최신 pykrx는 KRX 로그인이 필요하므로
환경변수 KRX_ID, KRX_PW를 설정해야 한다 (data.krx.co.kr 무료 회원).

캐시 CSV 형식 (flows_dir/<티커>.csv):
  Date, foreign, institution, short_ratio
  foreign/institution: 순매수 금액(원), short_ratio: 공매도 잔고 비중(%)
"""
from __future__ import annotations

import os
import sys
from typing import Dict, List

import pandas as pd


def _code(t: str) -> str:
    return t.split(".")[0]


def fetch_flows(tickers: List[str], start: str, end: str, cache_dir: str) -> None:
    """KR 티커(.KS/.KQ)만 pykrx로 받아 cache_dir에 저장. 실패한 종목은 건너뜀."""
    try:
        from pykrx import stock
    except ImportError:
        print("pykrx 미설치: pip install pykrx (수급 지표 생략)", file=sys.stderr)
        return
    os.makedirs(cache_dir, exist_ok=True)
    s, e = start.replace("-", ""), end.replace("-", "")
    for t in tickers:
        if not t.endswith((".KS", ".KQ")):
            continue
        try:
            tv = stock.get_market_trading_value_by_date(s, e, _code(t))
            out = pd.DataFrame(index=tv.index)
            out["foreign"] = tv.get("외국인합계", tv.get("외국인"))
            out["institution"] = tv.get("기관합계")
            try:
                sb = stock.get_shorting_balance_by_date(s, e, _code(t))
                out["short_ratio"] = sb.get("비중").reindex(out.index)
            except Exception:
                pass
            out.index.name = "Date"
            out.to_csv(os.path.join(cache_dir, f"{t}.csv"))
        except Exception as ex:  # 네트워크/로그인 실패
            print(f"[수급 실패] {t}: {type(ex).__name__} {str(ex)[:80]}", file=sys.stderr)


def load_flows(cache_dir: str) -> Dict[str, pd.DataFrame]:
    out: Dict[str, pd.DataFrame] = {}
    if not cache_dir or not os.path.isdir(cache_dir):
        return out
    for fn in os.listdir(cache_dir):
        if fn.endswith(".csv"):
            df = pd.read_csv(os.path.join(cache_dir, fn), parse_dates=["Date"]).set_index("Date").sort_index()
            out[fn[:-4]] = df
    return out
