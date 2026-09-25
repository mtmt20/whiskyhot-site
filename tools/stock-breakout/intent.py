#!/usr/bin/env python3
"""
주가 부양 의지 스캐너 (DART 공시 기반)

동서처럼 오너가 꾸준히 사 모으는 종목 중에서, 이제는 주가를 올리는 쪽으로
이해관계가 바뀐 종목을 찾는다. 승계를 위한 매수는 주가가 낮을수록 유리하지만,
승계가 끝났거나 회사가 자사주 소각·배당 확대에 나서면 방향이 바뀐다.

공시 제목으로 판정하는 신호 (최근 N개월)
  가점: 자기주식 취득결정, 주식 소각결정, 기업가치 제고계획(밸류업), 현금·현물배당 결정,
        임원·주요주주 소유보고(내부자 매매), 5% 대량보유 보고, 공개매수, 증여 완료 후 매수
  감점: 전환사채·신주인수권부사채·교환사채 발행, 유상증자, 자기주식 처분(시장 매도 물량)

사용 예 (DART 오픈API 키 필요, opendart.fss.or.kr 무료 발급)
  DART_API_KEY=키 python intent.py --months 12                 # 전 시장 최근 12개월 공시 스캔
  DART_API_KEY=키 python intent.py 026960 007310 --months 36   # 특정 종목 3년치
  python intent.py --with-accumulation                        # 상위 종목에 매집점수까지 붙이기
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

API = "https://opendart.fss.or.kr/api"

# (키워드, 신호명, 점수)  - 공시 제목(report_nm)에 키워드가 들어가면 해당 신호
RULES = [
    ("자기주식취득결정", "자사주 매입", 2.0),
    ("자기주식취득신탁계약체결", "자사주 신탁매입", 1.5),
    ("주식소각결정", "자사주 소각", 3.0),
    ("기업가치제고계획", "밸류업 계획", 2.0),
    ("현금ㆍ현물배당결정", "배당 결정", 0.5),
    ("임원ㆍ주요주주특정증권등소유상황보고서", "내부자 소유보고", 0.0),   # 방향은 elestock으로 판정
    ("주식등의대량보유상황보고서", "5% 대량보유", 0.5),
    ("공개매수신고서", "공개매수", 3.0),
    ("전환사채권발행결정", "전환사채 발행", -2.0),
    ("신주인수권부사채권발행결정", "BW 발행", -2.0),
    ("교환사채권발행결정", "교환사채 발행", -1.5),
    ("유상증자결정", "유상증자", -2.0),
    ("자기주식처분결정", "자사주 처분", -1.5),
]
PBLNTF_TYPES = ["B", "D", "I"]   # 주요사항보고, 지분공시, 거래소공시


def _get(path: str, params: dict) -> dict:
    import requests
    for i in range(4):
        try:
            r = requests.get(f"{API}/{path}", params=params, timeout=30)
            return r.json()
        except Exception:
            time.sleep(2 ** i)
    return {"status": "999", "message": "network"}


def fetch_list(key: str, bgn: str, end: str, corp_code: Optional[str] = None) -> List[dict]:
    """공시 목록. corp_code 없으면 DART 제한상 3개월 단위로 나눠 받는다."""
    out: List[dict] = []
    spans = []
    b, e = datetime.strptime(bgn, "%Y%m%d"), datetime.strptime(end, "%Y%m%d")
    step = timedelta(days=3650 if corp_code else 89)
    while b <= e:
        spans.append((b, min(b + step, e)))
        b = b + step + timedelta(days=1)
    for sb, se in spans:
        for ty in PBLNTF_TYPES:
            page = 1
            while True:
                p = {"crtfc_key": key, "bgn_de": sb.strftime("%Y%m%d"), "end_de": se.strftime("%Y%m%d"),
                     "pblntf_ty": ty, "page_no": page, "page_count": 100}
                if corp_code:
                    p["corp_code"] = corp_code
                r = _get("list.json", p)
                if r.get("status") != "000":
                    break
                out.extend(r.get("list", []))
                if page >= int(r.get("total_page", 1)):
                    break
                page += 1
    return out


def classify(filings: List[dict]) -> pd.DataFrame:
    rows = []
    for f in filings:
        name = f.get("report_nm", "").replace(" ", "")
        if "[기재정정]" in name or "[첨부정정]" in name:
            continue
        for kw, sig, pts in RULES:
            if kw in name:
                rows.append({"stock_code": f.get("stock_code", "").strip(), "corp_name": f.get("corp_name"),
                             "corp_code": f.get("corp_code"), "date": f.get("rcept_dt"), "signal": sig,
                             "points": pts, "report": f.get("report_nm"), "filer": f.get("flr_nm"),
                             "rcept_no": f.get("rcept_no")})
                break
    df = pd.DataFrame(rows)
    # 같은 결정이 주요사항보고와 거래소공시로 두 번 올라오는 경우 제거
    return df.drop_duplicates(subset=["stock_code", "signal", "date"]) if not df.empty else df


def insider_net(key: str, corp_code: str, since: str) -> dict:
    """임원·주요주주 소유보고 증감 합계 (since 이후)."""
    r = _get("elestock.json", {"crtfc_key": key, "corp_code": corp_code})
    if r.get("status") != "000":
        return {"insider_up": 0, "insider_down": 0, "insider_net": 0.0}
    df = pd.DataFrame(r["list"])
    df = df[df["rcept_dt"] >= since]
    ch = pd.to_numeric(df["sp_stock_lmp_irds_cnt"].astype(str).str.replace(",", ""), errors="coerce").fillna(0)
    return {"insider_up": int((ch > 0).sum()), "insider_down": int((ch < 0).sum()), "insider_net": float(ch.sum())}


def score_companies(sig: pd.DataFrame) -> pd.DataFrame:
    if sig.empty:
        return pd.DataFrame()
    sig = sig[sig["stock_code"] != ""]
    g = sig.groupby(["stock_code", "corp_name", "corp_code"])
    base = g["points"].sum().rename("공시점수").reset_index()
    piv = sig.pivot_table(index="stock_code", columns="signal", values="points", aggfunc="count", fill_value=0)
    return base.merge(piv, left_on="stock_code", right_index=True, how="left")


def corp_codes(key: str, cache_dir: str) -> Dict[str, str]:
    import io
    import zipfile
    import xml.etree.ElementTree as ET
    import requests
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, "dart_corpcode.json")
    if not os.path.exists(path):
        r = requests.get(f"{API}/corpCode.xml", params={"crtfc_key": key}, timeout=60)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        root = ET.fromstring(z.read(z.namelist()[0]))
        m = {e.findtext("stock_code").strip(): e.findtext("corp_code")
             for e in root.iter("list") if (e.findtext("stock_code") or "").strip()}
        with open(path, "w") as f:
            json.dump(m, f)
    with open(path) as f:
        return json.load(f)


def main() -> None:
    ap = argparse.ArgumentParser(description="주가 부양 의지 스캐너 (DART 공시)")
    ap.add_argument("codes", nargs="*", help="6자리 종목코드 (없으면 전 시장)")
    ap.add_argument("--months", type=int, default=12)
    ap.add_argument("--dart-key", default=os.environ.get("DART_API_KEY"))
    ap.add_argument("--cache-dir", default=os.path.join(HERE, "ohlcv_cache"))
    ap.add_argument("--top", type=int, default=40, help="내부자 매매·매집점수를 붙일 상위 종목 수")
    ap.add_argument("--with-accumulation", action="store_true", help="상위 종목에 매집점수(accumulation.py) 추가")
    ap.add_argument("--out", default="intent_scan.csv")
    a = ap.parse_args()
    if not a.dart_key:
        sys.exit("DART_API_KEY 환경변수 또는 --dart-key 가 필요합니다 (opendart.fss.or.kr 무료 발급).")

    end = datetime.now()
    bgn = end - timedelta(days=30 * a.months)
    b, e = bgn.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    filings: List[dict] = []
    if a.codes:
        cc = corp_codes(a.dart_key, a.cache_dir)
        for code in a.codes:
            if code in cc:
                filings += fetch_list(a.dart_key, b, e, cc[code])
    else:
        print(f"전 시장 공시 수집 중 ({b}~{e})...", file=sys.stderr)
        filings = fetch_list(a.dart_key, b, e)
    sig = classify(filings)
    res = score_companies(sig)
    if res.empty:
        sys.exit("해당 기간 신호 공시가 없습니다.")

    res = res.sort_values("공시점수", ascending=False)
    top = res.head(a.top).copy()
    ins = [insider_net(a.dart_key, c, b) for c in top["corp_code"]]
    top = pd.concat([top.reset_index(drop=True), pd.DataFrame(ins)], axis=1)
    # 오너 매수(내부자 순증) 가점, 순감 감점
    top["종합점수"] = top["공시점수"] + top["insider_net"].apply(lambda x: 2 if x > 0 else (-2 if x < 0 else 0))

    if a.with_accumulation:
        from accumulation import compute_series, load_daily, window_signals
        s_ = (end - timedelta(days=500)).strftime("%Y-%m-%d")
        accs = []
        for code in top["stock_code"]:
            df = load_daily(code, s_, end.strftime("%Y-%m-%d"), a.cache_dir)
            accs.append(window_signals(compute_series(df).iloc[-250:], None, None)["매집점수"]
                        if df is not None and len(df) > 260 else None)
        top["매집점수"] = accs

    top = top.sort_values("종합점수", ascending=False)
    top.to_csv(a.out, index=False, encoding="utf-8-sig")
    sig.to_csv(a.out.replace(".csv", "_filings.csv"), index=False, encoding="utf-8-sig")
    cols = ["stock_code", "corp_name", "종합점수", "공시점수", "insider_up", "insider_down"] + \
        [c for c in ("자사주 소각", "자사주 매입", "밸류업 계획", "공개매수", "전환사채 발행", "유상증자",
                     "자사주 처분", "매집점수") if c in top]
    with pd.option_context("display.width", 220, "display.max_columns", 30):
        print(top[cols].head(30).to_string(index=False))
    print(f"\n저장: {a.out} (종목별), {a.out.replace('.csv', '_filings.csv')} (근거 공시 목록)")


if __name__ == "__main__":
    main()
