#!/usr/bin/env python3
"""
DART 공시로 실험실용 이벤트 파일(events.csv: code,date,type,detail)을 만든다.

만드는 이벤트 (카탈로그 번호)
  insider_buy        임원·주요주주 지분 증가 보고 (증여로 보이는 주고받기는 제외)      37번
  insider_cluster    30일 안에 서로 다른 내부자 2명 이상 증가 보고                        37번
  earnings_up        정기보고서 영업이익(누적)이 전년 동기 대비 30% 이상 증가            32번
  turnaround         전년 동기 적자에서 흑자 전환                                        32번
  buyback / cancel / valueup / tender        자사주 매입, 소각, 밸류업 계획, 공개매수   41·42·45번
  cb / rights        전환사채·BW·교환사채 발행, 유상증자 (피할 신호 검증용)
  gift / gift_window_end    증여 보고일과 그 2개월 뒤 (오너 이해관계 전환 가설)         50번

사용 예
  DART_API_KEY=키 python events_from_dart.py --years 5            # 기본 유니버스
  DART_API_KEY=키 python events_from_dart.py 003540 214420
  python lab.py --events events.csv --note "공시 이벤트 첫 검증"
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from story import EVENT_RULES, Dart, num  # noqa: E402

TYPE_OF = {"자사주 매입": "buyback", "자사주 신탁매입": "buyback", "자사주 소각": "cancel",
           "밸류업 계획": "valueup", "공개매수": "tender", "전환사채 발행": "cb", "BW 발행": "cb",
           "교환사채 발행": "cb", "유상증자": "rights"}
REPORTS = [("11013", "1분기"), ("11012", "반기"), ("11014", "3분기"), ("11011", "사업")]


def insider_events(dart: Dart, code: str, cc: str, since: str) -> list:
    rows = [{"date": pd.to_datetime(r["rcept_dt"]), "who": r.get("repror", ""),
             "change": num(r.get("sp_stock_lmp_irds_cnt"))}
            for r in dart.rows("elestock.json", {"corp_code": cc})]
    df = pd.DataFrame(rows)
    if df.empty:
        return []
    df = df[(df["date"] >= pd.Timestamp(since)) & df["change"].notna()]
    out, buys = [], []
    for d, g in df.groupby("date"):
        dec = g[g["change"] < 0]["change"].abs().tolist()
        for _, r in g[g["change"] > 0].iterrows():
            if any(abs(x - r["change"]) <= 0.05 * x for x in dec):   # 같은 날 비슷한 수량 감소 = 증여 추정
                continue
            out.append({"code": code, "date": d, "type": "insider_buy", "detail": f"{r['who']} +{r['change']:,.0f}주"})
            buys.append((d, r["who"]))
    buys.sort()
    last_cluster = None
    for i, (d, w) in enumerate(buys):
        others = {x[1] for x in buys[:i] if (d - x[0]).days <= 30 and x[1] != w}
        if others and (last_cluster is None or (d - last_cluster).days > 30):
            out.append({"code": code, "date": d, "type": "insider_cluster",
                        "detail": f"{w} 외 {len(others)}명 30일 내 매수"})
            last_cluster = d
    return out


def filing_events(dart: Dart, code: str, cc: str, since: str, today: date) -> list:
    out = []
    for ty in ("B", "I"):
        page = 1
        while True:
            r = dart.get("list.json", {"corp_code": cc, "bgn_de": since, "end_de": today.strftime("%Y%m%d"),
                                       "pblntf_ty": ty, "page_no": page, "page_count": 100})
            if r.get("status") != "000":
                break
            for x in r.get("list", []):
                nm = x.get("report_nm", "").replace(" ", "")
                if "정정]" in nm:
                    continue
                for kw, label, _ in EVENT_RULES:
                    if kw in nm and label in TYPE_OF:
                        out.append({"code": code, "date": pd.to_datetime(x["rcept_dt"]), "type": TYPE_OF[label],
                                    "detail": x.get("report_nm", "")})
                        break
            if page >= int(r.get("total_page", 1)):
                break
            page += 1
    return out


def _amount(r: dict, first: str, fallback: str) -> float:
    v = num(r.get(first))
    return v if not pd.isna(v) else num(r.get(fallback))


def earnings_events(dart: Dart, code: str, cc: str, years: range) -> list:
    out = []
    for y in years:
        for rc, label in REPORTS:
            rows = dart.rows("fnlttSinglAcnt.json", {"corp_code": cc, "bsns_year": str(y), "reprt_code": rc})
            pick = [r for r in rows if r.get("account_nm", "").replace(" ", "") == "영업이익"]
            pick = [r for r in pick if r.get("fs_div") == "CFS"] or [r for r in pick if r.get("fs_div") == "OFS"]
            if not pick:
                continue
            r = pick[0]
            cur = _amount(r, "thstrm_add_amount", "thstrm_amount")      # 누적 우선 (전년 동기 누적과 비교)
            prev = _amount(r, "frmtrm_add_amount", "frmtrm_amount")
            if pd.isna(cur) or pd.isna(prev) or not r.get("rcept_no"):
                continue
            d = pd.to_datetime(str(r["rcept_no"])[:8])
            if prev < 0 < cur:
                out.append({"code": code, "date": d, "type": "turnaround", "detail": f"{y} {label} 흑자전환"})
            elif prev > 0 and cur / prev - 1 >= 0.30:
                out.append({"code": code, "date": d, "type": "earnings_up",
                            "detail": f"{y} {label} 영업이익 {cur/prev-1:+.0%}"})
    return out


def gift_events(dart: Dart, code: str, cc: str, since: str) -> list:
    out = []
    for r in dart.rows("majorstock.json", {"corp_code": cc}):
        d = pd.to_datetime(r["rcept_dt"])
        if d < pd.Timestamp(since):
            continue
        if any(k in str(r.get("report_resn", "")) for k in ("증여", "수증")):
            out.append({"code": code, "date": d, "type": "gift", "detail": r.get("repror", "")})
            out.append({"code": code, "date": d + pd.DateOffset(months=2), "type": "gift_window_end",
                        "detail": r.get("repror", "")})
    return out


def main():
    ap = argparse.ArgumentParser(description="DART 공시로 실험실 이벤트 파일 만들기")
    ap.add_argument("codes", nargs="*", help="6자리 코드 (없으면 기본 한국 유니버스)")
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--dart-key", default=os.environ.get("DART_API_KEY"))
    ap.add_argument("--cache-dir", default=os.path.join(HERE, "dart_cache"))
    ap.add_argument("--fixture-dir", help="오프라인 테스트용 DART 응답 폴더")
    ap.add_argument("--today", help="기준일 YYYY-MM-DD")
    ap.add_argument("--out", default="events.csv")
    a = ap.parse_args()
    offline = bool(a.fixture_dir)
    if not offline and not a.dart_key:
        sys.exit("DART_API_KEY 가 필요합니다 (opendart.fss.or.kr 무료 발급).")
    dart = Dart(a.dart_key, a.fixture_dir or a.cache_dir, offline)
    today = pd.Timestamp(a.today).date() if a.today else date.today()
    since = date(today.year - a.years, today.month, 1).strftime("%Y%m%d")
    codes = a.codes
    if not codes:
        from breakout_finder import KR_UNIVERSE
        codes = [t.split(".")[0] for t in KR_UNIVERSE]
    ev = []
    for i, code in enumerate(codes):
        cc = dart.corp_code(code)
        if not cc:
            print(f"{code}: 기업코드 없음", file=sys.stderr)
            continue
        ev += insider_events(dart, code, cc, since)
        ev += filing_events(dart, code, cc, since, today)
        ev += earnings_events(dart, code, cc, range(today.year - a.years, today.year + 1))
        ev += gift_events(dart, code, cc, since)
        if (i + 1) % 10 == 0:
            print(f"  {i+1}/{len(codes)} 종목", file=sys.stderr)
    df = pd.DataFrame(ev)
    if df.empty:
        sys.exit("이벤트가 없습니다.")
    df = df[df["date"] <= pd.Timestamp(today)].drop_duplicates(["code", "date", "type"]).sort_values(["date", "code"])
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    df.to_csv(a.out, index=False, encoding="utf-8-sig")
    print(df["type"].value_counts().rename("건수").to_string())
    print(f"\n저장: {a.out}  →  python lab.py --events {a.out}")


if __name__ == "__main__":
    main()
