#!/usr/bin/env python3
"""
오너 이해관계 스토리 분석기

종목코드를 넣으면 DART 공시로 5년치 이력을 모아 "오너가 지금 주가가 오르길 원하는가"를
이야기 형태 보고서로 정리한다.

모으는 데이터 (DART 오픈API)
  최대주주 현황(사업보고서)   연도별 오너 일가 개인별 지분 → 승계 단계
  배당에 관한 사항            주당배당금, 배당성향, 시가배당률 추이
  자기주식 취득·처분 현황      자사주 매입·처분·소각
  대량보유 상황보고(5%)       보고사유: 장내매수, 증여, 수증 등
  임원·주요주주 소유보고       내부자 증감
  공시 목록                   자사주 소각·매입, 밸류업, 배당, 전환사채, 유상증자, 최대주주 변경 등
  (선택) 일봉                 주가 위치, 매집점수

판정 규칙
  1. 최근 증여가 있고 평가기간(증여일 ±2개월)이나 취소기한(증여월 말일+3개월) 안이면
     → 당분간 낮은 주가가 유리
  2. 내부자 순매도나 전환사채·유상증자 물량이 우세하면 → 매도 우위 또는 대기 물량
  3. 승계가 초기·진행 단계면 → 배당은 원하지만 주가 급등은 아직 불리
  4. 승계 완료(또는 해당 없음) + 주주환원 강화 + 오너 매수 → 주가 상승 이해관계 일치

사용 예
  DART_API_KEY=키 python story.py 003540 001800 214420
  DART_API_KEY=키 python story.py 214420 --years 5 --out-dir stories
  python story.py 999990 --fixture-dir ./fixtures     # 오프라인 테스트
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import List, Optional

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

API = "https://opendart.fss.or.kr/api"

# 공시 제목 분류 규칙 (키워드, 이벤트, 성격)  성격: + 주가 우호, - 비우호, 0 중립
EVENT_RULES = [
    ("주식소각결정", "자사주 소각", "+"),
    ("자기주식취득신탁계약체결", "자사주 신탁매입", "+"),
    ("자기주식취득결정", "자사주 매입", "+"),
    ("기업가치제고계획", "밸류업 계획", "+"),
    ("공개매수신고서", "공개매수", "+"),
    ("현금ㆍ현물배당결정", "배당 결정", "0"),
    ("최대주주변경", "최대주주 변경", "0"),
    ("최대주주등소유주식변동신고서", "최대주주 지분 변동", "0"),
    ("전환사채권발행결정", "전환사채 발행", "-"),
    ("신주인수권부사채권발행결정", "BW 발행", "-"),
    ("교환사채권발행결정", "교환사채 발행", "-"),
    ("유상증자결정", "유상증자", "-"),
    ("자기주식처분결정", "자사주 처분", "-"),
    ("회사분할결정", "회사 분할", "0"),
    ("회사합병결정", "합병", "0"),
]
NEXT_GEN = re.compile(r"자녀|장남|차남|삼남|장녀|차녀|삼녀|아들|딸|손자|손녀|^자$|^녀$|子|女")
NON_FAMILY = re.compile(r"계열|법인|재단|임원|자기주식|우리사주|조합|펀드|복지")
OLDER_GEN = re.compile(r"^부$|^모$|부친|모친|조부|조모|^父$|^母$")


def josa(word: str, a: str, b: str) -> str:
    """받침 유무에 따라 조사 선택. josa('양부', '으로', '로') → '양부로'."""
    if not word:
        return word + b
    ch = word[-1]
    if not ("가" <= ch <= "힣"):
        return word + b
    jong = (ord(ch) - 0xAC00) % 28
    if a == "으로" and jong == 8:          # ㄹ 받침은 '로'
        return word + b
    return word + (a if jong else b)


def num(x) -> float:
    if x is None:
        return np.nan
    s = str(x).replace(",", "").strip()
    if s in ("", "-", "N/A"):
        return np.nan
    try:
        return float(s)
    except ValueError:
        return np.nan


# --------------------------------------------------------------------------- #
# DART 클라이언트 (응답을 JSON으로 캐시, 오프라인 fixture 지원)
# --------------------------------------------------------------------------- #
class Dart:
    def __init__(self, key: Optional[str], cache_dir: str, offline: bool = False):
        self.key, self.cache_dir, self.offline = key, cache_dir, offline
        os.makedirs(cache_dir, exist_ok=True)

    @staticmethod
    def cache_name(path: str, params: dict) -> str:
        p = {k: v for k, v in sorted(params.items()) if k != "crtfc_key"}
        h = hashlib.md5(json.dumps(p, ensure_ascii=False).encode()).hexdigest()[:10]
        return f"{path.replace('.json', '')}_{p.get('corp_code', '')}_{p.get('bsns_year', '')}_{h}.json"

    def get(self, path: str, params: dict) -> dict:
        fn = os.path.join(self.cache_dir, self.cache_name(path, params))
        if os.path.exists(fn):
            with open(fn, encoding="utf-8") as f:
                return json.load(f)
        if self.offline or not self.key:
            return {"status": "013", "list": []}
        import requests
        r = {"status": "999"}
        for i in range(4):
            try:
                r = requests.get(f"{API}/{path}", params={"crtfc_key": self.key, **params}, timeout=30).json()
                break
            except Exception:
                time.sleep(2 ** i)
        if r.get("status") in ("000", "013"):          # 013 = 데이터 없음도 캐시
            with open(fn, "w", encoding="utf-8") as f:
                json.dump(r, f, ensure_ascii=False)
        return r

    def rows(self, path: str, params: dict) -> List[dict]:
        r = self.get(path, params)
        return r.get("list", []) if r.get("status") == "000" else []

    def corp_code(self, stock_code: str) -> Optional[str]:
        fn = os.path.join(self.cache_dir, "dart_corpcode.json")
        if not os.path.exists(fn):
            if self.offline or not self.key:
                return None
            import io
            import zipfile
            import xml.etree.ElementTree as ET
            import requests
            r = requests.get(f"{API}/corpCode.xml", params={"crtfc_key": self.key}, timeout=60)
            root = ET.fromstring(zipfile.ZipFile(io.BytesIO(r.content)).read("CORPCODE.xml"))
            m = {e.findtext("stock_code").strip(): e.findtext("corp_code")
                 for e in root.iter("list") if (e.findtext("stock_code") or "").strip()}
            with open(fn, "w", encoding="utf-8") as f:
                json.dump(m, f)
        with open(fn, encoding="utf-8") as f:
            return json.load(f).get(stock_code)


# --------------------------------------------------------------------------- #
# 사실 수집
# --------------------------------------------------------------------------- #
@dataclass
class Facts:
    code: str
    corp_code: str
    name: str = ""
    holders: pd.DataFrame = field(default_factory=pd.DataFrame)
    dividends: pd.DataFrame = field(default_factory=pd.DataFrame)
    treasury: pd.DataFrame = field(default_factory=pd.DataFrame)
    events: pd.DataFrame = field(default_factory=pd.DataFrame)
    major: pd.DataFrame = field(default_factory=pd.DataFrame)
    insider: pd.DataFrame = field(default_factory=pd.DataFrame)
    price: Optional[pd.DataFrame] = None


def collect(dart: Dart, code: str, years: int, today: date, price_dir: Optional[str]) -> Optional[Facts]:
    cc = dart.corp_code(code)
    if not cc:
        return None
    f = Facts(code=code, corp_code=cc)
    last_year = today.year - 1
    yrs = list(range(last_year - years + 1, last_year + 1))

    hold, div, tre = [], [], []
    for y in yrs:
        base = {"corp_code": cc, "bsns_year": str(y), "reprt_code": "11011"}
        for r in dart.rows("hyslrSttus.json", base):
            if "보통" not in (r.get("stock_knd") or "보통"):
                continue
            hold.append({"year": y, "name": r.get("nm", "").strip(), "relate": (r.get("relate") or "").strip(),
                         "shares": num(r.get("trmend_posesn_stock_co")),
                         "pct": num(r.get("trmend_posesn_stock_qota_rt")), "note": r.get("rm", "")})
        d = {"year": y}
        for r in dart.rows("alotMatter.json", base):
            se, kind = r.get("se", ""), r.get("stock_knd") or ""
            if "주당 현금배당금" in se and "보통" in kind:
                d["dps"] = num(r.get("thstrm"))
            elif "현금배당성향" in se:
                d["payout"] = num(r.get("thstrm"))
            elif "현금배당수익률" in se and "보통" in kind:
                d["yield"] = num(r.get("thstrm"))
        if len(d) > 1:
            div.append(d)
        for r in dart.rows("tesstkAcqsDspsSttus.json", base):
            if (r.get("acqs_mth1") or "").strip() == "총계" and "보통" in (r.get("stock_knd") or "보통"):
                tre.append({"year": y, "begin": num(r.get("bsis_qy")), "acquired": num(r.get("change_qy_acqs")),
                            "disposed": num(r.get("change_qy_dsps")), "cancelled": num(r.get("change_qy_incnr")),
                            "end": num(r.get("trmend_qy"))})
    f.holders, f.dividends, f.treasury = pd.DataFrame(hold), pd.DataFrame(div), pd.DataFrame(tre)

    bgn = date(today.year - years, today.month, 1).strftime("%Y%m%d")
    filings = []
    for ty in ("B", "D", "I"):
        page = 1
        while True:
            r = dart.get("list.json", {"corp_code": cc, "bgn_de": bgn, "end_de": today.strftime("%Y%m%d"),
                                       "pblntf_ty": ty, "page_no": page, "page_count": 100})
            if r.get("status") != "000":
                break
            filings += r.get("list", [])
            if page >= int(r.get("total_page", 1)):
                break
            page += 1
    if filings:
        f.name = filings[0].get("corp_name", "")
    ev = []
    for x in filings:
        nm = x.get("report_nm", "").replace(" ", "")
        if "정정]" in nm:
            continue
        for kw, label, tone in EVENT_RULES:
            if kw in nm:
                ev.append({"date": pd.to_datetime(x["rcept_dt"]), "event": label, "tone": tone,
                           "report": x.get("report_nm", ""), "filer": x.get("flr_nm", "")})
                break
    f.events = pd.DataFrame(ev).drop_duplicates(["date", "event"]).sort_values("date") if ev else pd.DataFrame()

    mj = [{"date": pd.to_datetime(r["rcept_dt"]), "who": r.get("repror", ""), "shares": num(r.get("stkqy")),
           "change": num(r.get("stkqy_irds")), "pct": num(r.get("stkrt")), "reason": r.get("report_resn", "")}
          for r in dart.rows("majorstock.json", {"corp_code": cc})]
    f.major = pd.DataFrame(mj)
    if not f.major.empty:
        f.major = f.major[f.major["date"] >= pd.Timestamp(bgn)].sort_values("date")
    ins = [{"date": pd.to_datetime(r["rcept_dt"]), "who": r.get("repror", ""),
            "change": num(r.get("sp_stock_lmp_irds_cnt")), "held": num(r.get("sp_stock_lmp_cnt"))}
           for r in dart.rows("elestock.json", {"corp_code": cc})]
    f.insider = pd.DataFrame(ins)
    if not f.insider.empty:
        f.insider = f.insider[f.insider["date"] >= pd.Timestamp(bgn)].sort_values("date")

    if price_dir or not dart.offline:
        try:
            from accumulation import load_daily
            s = (today - timedelta(days=365 * years + 30)).strftime("%Y-%m-%d")
            f.price = load_daily(code, s, today.strftime("%Y-%m-%d"),
                                 price_dir or os.path.join(HERE, "ohlcv_cache"), offline=bool(price_dir))
        except Exception:
            f.price = None
    if not f.name:
        f.name = code
    return f


# --------------------------------------------------------------------------- #
# 분석
# --------------------------------------------------------------------------- #
def gift_tax(value: float, controlling: bool = True) -> float:
    """증여세 산출세액 추정 (공제 미반영). 최대주주 할증 20%."""
    t = value * (1.2 if controlling else 1.0)
    for lim, rate, ded in ((1e8, .1, 0), (5e8, .2, 1e7), (1e9, .3, 6e7), (3e9, .4, 1.6e8), (np.inf, .5, 4.6e8)):
        if t <= lim:
            return max(t * rate - ded, 0)
    return 0.0


def month_end_plus(d: pd.Timestamp, months: int) -> pd.Timestamp:
    y, m = d.year, d.month + months
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return pd.Timestamp(y, m, calendar.monthrange(y, m)[1])


def price_on(f: Facts, d: pd.Timestamp) -> float:
    if f.price is None or f.price.empty:
        return np.nan
    p = f.price["Close"]
    p = p[p.index <= d]
    return float(p.iloc[-1]) if len(p) else np.nan


def find_gifts(f: Facts) -> List[dict]:
    gifts = []
    if not f.major.empty:
        for _, r in f.major.iterrows():
            if re.search(r"증여|수증", str(r["reason"])):
                gifts.append({"date": r["date"], "who": r["who"], "shares": abs(r["change"]) if pd.notna(r["change"]) else np.nan,
                              "src": "대량보유 보고", "reason": r["reason"]})
    if not f.insider.empty:        # 같은 날 한 명 감소 + 다른 사람 비슷한 수량 증가 → 증여로 추정
        for d, g in f.insider.groupby("date"):
            dec, inc = g[g["change"] < 0], g[g["change"] > 0]
            for _, a in dec.iterrows():
                for _, b in inc.iterrows():
                    if abs(abs(a["change"]) - b["change"]) <= 0.05 * abs(a["change"]):
                        if not any(abs((x["date"] - d).days) <= 7 for x in gifts):
                            gifts.append({"date": d, "who": f"{a['who']} → {b['who']}", "shares": b["change"],
                                          "src": "소유보고 대응", "reason": "증여 추정"})
    out = []
    for gft in sorted(gifts, key=lambda x: x["date"]):
        px = price_on(f, gft["date"])
        val = gft["shares"] * px if pd.notna(px) and pd.notna(gft["shares"]) else np.nan
        gft.update(price=px, value=val, tax=gift_tax(val) if pd.notna(val) else np.nan,
                   valuation_end=gft["date"] + pd.DateOffset(months=2),
                   cancel_deadline=month_end_plus(gft["date"], 3))
        out.append(gft)
    return out


def succession(f: Facts) -> dict:
    h = f.holders
    if h.empty:
        return {"stage": "정보 없음", "detail": "사업보고서 최대주주 현황을 받지 못했습니다."}
    h = h[~h["name"].isin(["계", "합계", "소계"])]
    fam = h[~h["relate"].str.contains(NON_FAMILY) & ~h["name"].str.contains(NON_FAMILY)]
    if fam.empty:
        return {"stage": "해당 없음", "detail": "최대주주 측이 법인 위주라 오너 개인 승계 구조가 아닙니다."}
    by_year = []
    for y, g in fam.groupby("year"):
        tot = g["shares"].sum()
        nxt = g[g["relate"].str.contains(NEXT_GEN)]["shares"].sum()
        top = g.sort_values("shares", ascending=False).iloc[0]
        by_year.append({"year": y, "family_pct": g["pct"].sum(), "next_gen_share": nxt / tot if tot else np.nan,
                        "top": top["name"], "top_relate": top["relate"], "top_pct": top["pct"]})
    t = pd.DataFrame(by_year).sort_values("year")
    first, last = t.iloc[0], t.iloc[-1]
    changed_top = first["top"] != last["top"]
    ng = last["next_gen_share"]
    last_fam = fam[fam["year"] == last["year"]]
    older_present = last_fam["relate"].str.contains(OLDER_GEN).any()   # 최대주주가 이미 자녀 세대
    if changed_top and last["top"] in set(fam[fam["relate"].str.contains(NEXT_GEN)]["name"]):
        stage = "완료"
    elif older_present and ng < 0.2:
        stage = "완료"
    elif ng >= 0.5:
        stage = "완료"
    elif ng >= 0.2 or (ng - first["next_gen_share"]) >= 0.05:
        stage = "진행"
    else:
        stage = "초기"
    return {"stage": stage, "table": t, "next_gen_share": ng, "family_pct": last["family_pct"],
            "top": last["top"], "top_pct": last["top_pct"], "changed_top": changed_top, "first_top": first["top"],
            "older_present": bool(older_present), "report_year": int(last["year"]),
            "top_shares": float(last_fam.sort_values("shares", ascending=False)["shares"].iloc[0]),
            "next_gen_names": set(fam[fam["relate"].str.contains(NEXT_GEN)]["name"])}


def apply_recent_gifts(suc: dict, gifts: List[dict], major: pd.DataFrame) -> dict:
    """사업보고서 이후 증여로 자녀 세대가 최대주주가 됐으면 승계 완료로 갱신."""
    if "table" not in suc or major.empty:
        return suc
    cutoff = pd.Timestamp(suc["report_year"], 12, 31)
    total = suc["top_shares"] / (suc["top_pct"] / 100) if suc["top_pct"] else np.nan
    for g in gifts:
        if g["date"] <= cutoff or g["src"] != "대량보유 보고":
            continue
        row = major[(major["date"] == g["date"]) & (major["who"] == g["who"])]
        if row.empty or g["who"] not in suc["next_gen_names"]:
            continue
        recv_pct = float(row["pct"].iloc[0])
        giver_left = suc["top_pct"] - g["shares"] / total * 100 if total == total else np.nan
        if pd.notna(giver_left) and recv_pct > giver_left:
            suc = {**suc, "stage": "완료", "changed_top": True, "first_top": suc["top"],
                   "top": g["who"], "top_pct": recv_pct,
                   "recent_gift_note": f"{g['date'].date()} 증여로 {g['who']}의 지분이 {recv_pct:.2f}%가 되어 "
                                       f"최대 지분 보유자가 바뀐 것으로 보입니다."}
    return suc


def owner_trading(f: Facts, gifts: List[dict], since: pd.Timestamp) -> dict:
    buy_cnt = sell_cnt = 0
    buy_sh = sell_sh = 0.0
    buyers = set()
    if not f.major.empty:
        m = f.major[f.major["date"] >= since]
        for _, r in m.iterrows():
            reason = str(r["reason"])
            if "장내매수" in reason and (pd.isna(r["change"]) or r["change"] > 0):
                buy_cnt += 1
                buy_sh += max(r["change"], 0) if pd.notna(r["change"]) else 0
                buyers.add(r["who"])
            elif "장내매도" in reason:
                sell_cnt += 1
                sell_sh += abs(r["change"]) if pd.notna(r["change"]) else 0
    gift_days = {g["date"] for g in gifts}
    ins_net = 0.0
    if not f.insider.empty:
        x = f.insider[(f.insider["date"] >= since) & ~f.insider["date"].isin(gift_days)]
        ins_net = float(x["change"].sum())
        if buy_cnt == 0:
            buy_cnt = int((x["change"] > 0).sum())
            buyers |= set(x[x["change"] > 0]["who"])
        if sell_cnt == 0:
            sell_cnt = int((x["change"] < 0).sum())
    return {"buy_cnt": buy_cnt, "sell_cnt": sell_cnt, "buy_sh": buy_sh, "sell_sh": sell_sh,
            "insider_net": ins_net, "buyers": sorted(buyers)}


def shareholder_return(f: Facts, since: pd.Timestamp) -> dict:
    ev = f.events[f.events["date"] >= since] if not f.events.empty else pd.DataFrame(columns=["event", "tone"])
    cnt = ev["event"].value_counts().to_dict() if not ev.empty else {}
    d = f.dividends.dropna(subset=["dps"]) if "dps" in f.dividends else pd.DataFrame()
    out = {"cancel": cnt.get("자사주 소각", 0), "buyback": cnt.get("자사주 매입", 0) + cnt.get("자사주 신탁매입", 0),
           "valueup": cnt.get("밸류업 계획", 0), "tender": cnt.get("공개매수", 0),
           "cb": cnt.get("전환사채 발행", 0) + cnt.get("BW 발행", 0) + cnt.get("교환사채 발행", 0),
           "rights": cnt.get("유상증자", 0), "dispose": cnt.get("자사주 처분", 0)}
    if len(d) >= 2 and d["dps"].iloc[0] > 0:
        yrs = d["year"].iloc[-1] - d["year"].iloc[0]
        out["dps_first"], out["dps_last"] = d["dps"].iloc[0], d["dps"].iloc[-1]
        out["dps_years"] = (d["year"].iloc[0], d["year"].iloc[-1])
        out["dps_cagr"] = (d["dps"].iloc[-1] / d["dps"].iloc[0]) ** (1 / max(yrs, 1)) - 1
        out["dps_cut"] = d["dps"].iloc[-1] < d["dps"].iloc[-2]
    if "payout" in f.dividends and f.dividends["payout"].notna().sum() >= 2:
        p = f.dividends.dropna(subset=["payout"])
        out["payout_first"], out["payout_last"] = p["payout"].iloc[0], p["payout"].iloc[-1]
    if "yield" in f.dividends and f.dividends["yield"].notna().any():
        out["yield_last"] = f.dividends.dropna(subset=["yield"])["yield"].iloc[-1]
    if not f.treasury.empty:
        out["treasury_cancelled"] = f.treasury["cancelled"].fillna(0).sum()
        out["treasury_end"] = f.treasury["end"].iloc[-1]
    strong = (out["cancel"] > 0 or out["valueup"] > 0 or out["tender"] > 0
              or out.get("dps_cagr", 0) >= 0.10
              or (out.get("payout_last", 0) - out.get("payout_first", 0)) >= 10
              or out.get("treasury_cancelled", 0) > 0)
    out["strong"] = bool(strong and not out.get("dps_cut", False)) or out["cancel"] > 0 or out["tender"] > 0
    out["weak_signal"] = out.get("dps_cut", False)
    return out


def price_context(f: Facts) -> dict:
    if f.price is None or f.price.empty or len(f.price) < 60:
        return {}
    c = f.price["Close"]
    out = {"last": float(c.iloc[-1]), "ret_total": float(c.iloc[-1] / c.iloc[0] - 1),
           "from_high": float(c.iloc[-1] / c.max() - 1), "high": float(c.max()), "high_date": c.idxmax(),
           "start": c.index[0]}
    try:
        from accumulation import compute_series, window_signals
        if len(f.price) > 260:
            out["accum"] = window_signals(compute_series(f.price).iloc[-250:], None, None)["매집점수"]
    except Exception:
        pass
    return out


def judge(f: Facts, today: date, years: int) -> dict:
    since = pd.Timestamp(today) - pd.DateOffset(years=years)
    recent = pd.Timestamp(today) - pd.DateOffset(years=2)
    gifts = find_gifts(f)
    suc = apply_recent_gifts(succession(f), gifts, f.major)
    own = owner_trading(f, gifts, recent)
    ret = shareholder_return(f, since)
    ret_recent = shareholder_return(f, recent)
    px = price_context(f)
    t = pd.Timestamp(today)
    active_gift = [g for g in gifts if g["cancel_deadline"] >= t]

    reasons = []
    if active_gift:
        g = active_gift[-1]
        verdict, tone = "당분간 낮은 주가가 유리", "down"
        reasons.append(f"{g['date'].date()} 증여의 세금 평가기간이 {g['valuation_end'].date()}까지, "
                       f"취소 가능 기한이 {g['cancel_deadline'].date()}까지입니다.")
    elif own["sell_cnt"] > own["buy_cnt"] and own["insider_net"] < 0 or ret_recent["cb"] + ret_recent["rights"] >= 2:
        verdict, tone = "매도 우위 또는 대기 물량", "down"
        if own["insider_net"] < 0:
            reasons.append("최근 2년 내부자 보고가 순감소입니다.")
        if ret_recent["cb"] + ret_recent["rights"]:
            reasons.append("최근 2년 전환사채·유상증자 공시가 있어 대기 물량이 생겼습니다.")
    elif suc["stage"] in ("초기", "진행"):
        if ret["strong"]:
            verdict, tone = "배당은 원하지만 주가 급등은 아직 불리", "mixed"
            reasons.append("주주환원은 강화됐지만 승계가 끝나지 않아 증여 시점에는 낮은 주가가 유리합니다.")
        else:
            verdict, tone = "승계 진행 중, 낮은 주가가 유리", "down"
            reasons.append("승계가 진행 중이고 주주환원 강화 신호가 약합니다.")
    elif ret["strong"] and own["buy_cnt"] > 0:
        verdict, tone = "주가 상승 이해관계 일치", "up"
        reasons.append("승계 부담이 없거나 끝났고, 회사의 주주환원과 오너 매수가 함께 나타납니다.")
    elif ret["strong"]:
        verdict, tone = "주주환원 기조, 상승 우호", "up"
        reasons.append("주주환원은 강하지만 오너의 추가 매수는 확인되지 않았습니다.")
    else:
        verdict, tone = "뚜렷한 신호 없음", "neutral"
    if ret.get("dps_cut"):
        reasons.append("최근 배당이 줄었습니다.")

    checkpoints = []
    for g in active_gift:
        checkpoints.append((g["valuation_end"], "증여세 평가기간 종료. 이후 주가를 누를 이유가 약해짐"))
        checkpoints.append((g["cancel_deadline"], "증여 취소 가능 기한. 이후 배당·자사주·오너 매수 공시 확인"))
    checkpoints.append((pd.Timestamp(today.year + (1 if today.month > 3 else 0), 3, 31), "사업보고서 제출. 최대주주 현황·배당 갱신"))
    return {"gifts": gifts, "succession": suc, "owner": own, "returns": ret, "returns_recent": ret_recent,
            "price": px, "verdict": verdict, "tone": tone, "reasons": reasons,
            "checkpoints": sorted(checkpoints, key=lambda x: x[0])}


# --------------------------------------------------------------------------- #
# 이야기 쓰기
# --------------------------------------------------------------------------- #
def fmt_won(x: float) -> str:
    if pd.isna(x):
        return "?"
    return f"{x/1e8:,.0f}억 원" if abs(x) >= 1e8 else f"{x:,.0f}원"


def write_story(f: Facts, j: dict, years: int) -> str:
    L = [f"## {f.name} ({f.code})", "", f"**판단: {j['verdict']}**", ""]
    L += [f"- {r}" for r in j["reasons"]] + [""]

    s = j["succession"]
    L.append("### 오너와 승계")
    if "table" in s:
        L.append(f"- {s['report_year']}년 사업보고서 기준 최대주주 측 합계 지분은 {s['family_pct']:.1f}%입니다.")
        if s.get("recent_gift_note"):
            L.append(f"- {s['recent_gift_note']}")
        elif s["changed_top"]:
            L.append(f"- {years}년 사이 최대 지분 보유자가 {josa(s['first_top'], '에서', '에서')} "
                     f"{josa(s['top'], '으로', '로')} 바뀌었습니다.")
        L.append(f"- 현재 가장 많이 가진 사람은 {josa(s['top'], '으로', '로')} {s['top_pct']:.2f}%입니다.")
        if s.get("older_present"):
            L.append("- 부모 세대가 특수관계인으로 표시돼 있어, 현 최대주주가 이미 자녀 세대입니다.")
        who = "현 최대주주 자녀 세대" if s.get("older_present") else "다음 세대"
        basis = "사업보고서 기준 " if s.get("recent_gift_note") else ""
        L.append(f"- {basis}{who} 지분 비중은 오너 일가 보유분의 {s['next_gen_share']*100:.0f}%입니다. "
                 f"승계 단계는 **{s['stage']}**로 판단합니다.")
    else:
        L.append(f"- {s['detail']}")
    o = j["owner"]
    if o["buy_cnt"]:
        who = ", ".join(o["buyers"][:4]) if o["buyers"] else "내부자"
        L.append(f"- 최근 2년 장내매수·지분 증가 보고가 {o['buy_cnt']}건입니다. 매수자는 {who}입니다.")
    if o["sell_cnt"]:
        L.append(f"- 최근 2년 장내매도·지분 감소 보고가 {o['sell_cnt']}건입니다.")
    for g in j["gifts"][-3:]:
        L.append(f"- {g['date'].date()} 증여: {g['who']}, {g['shares']:,.0f}주"
                 + (f", 당시 주가 {g['price']:,.0f}원 기준 약 {fmt_won(g['value'])}, "
                    f"추정 증여세 {fmt_won(g['tax'])}" if pd.notna(g.get("value")) else "") + ".")
    L.append("")

    r = j["returns"]
    L.append("### 주주환원")
    if "dps_first" in r:
        y0, y1 = r["dps_years"]
        L.append(f"- 주당 배당금은 {y0}년 {r['dps_first']:,.0f}원에서 {y1}년 {r['dps_last']:,.0f}원이 됐습니다. "
                 f"연평균 {r['dps_cagr']*100:+.0f}%입니다.")
    if "payout_first" in r:
        L.append(f"- 배당성향은 {r['payout_first']:.0f}%에서 {r['payout_last']:.0f}%로 바뀌었습니다.")
    if "yield_last" in r:
        L.append(f"- 최근 시가배당률은 {r['yield_last']:.1f}%입니다.")
    items = [(r["cancel"], "자사주 소각"), (r["buyback"], "자사주 매입"), (r["valueup"], "밸류업 계획"),
             (r["tender"], "공개매수")]
    got = [f"{n} {c}건" for c, n in items if c]
    if got:
        L.append(f"- {years}년간 공시: " + ", ".join(got) + ".")
    if r.get("treasury_cancelled", 0) > 0:
        L.append(f"- 사업보고서 기준 소각된 자사주는 {r['treasury_cancelled']:,.0f}주입니다.")
    neg = [(r["cb"], "전환사채·BW·교환사채 발행"), (r["rights"], "유상증자"), (r["dispose"], "자사주 처분")]
    bad = [f"{n} {c}건" for c, n in neg if c]
    if bad:
        L.append("- 주가에 부담이 되는 공시: " + ", ".join(bad) + ".")
    L.append("")

    p = j["price"]
    if p:
        L.append("### 주가")
        L.append(f"- {p['start'].date()} 이후 수익률 {p['ret_total']*100:+.0f}%, 현재가 {p['last']:,.0f}원입니다. "
                 f"기간 고점 {p['high']:,.0f}원({p['high_date'].date()}) 대비 {p['from_high']*100:+.0f}%입니다.")
        if "accum" in p:
            L.append(f"- 최근 1년 매집점수는 {p['accum']:.0f}점입니다.")
        L.append("")

    ev = f.events[f.events["event"] != "배당 결정"].tail(12) if not f.events.empty else pd.DataFrame()
    if not ev.empty:
        L.append("### 주요 공시 타임라인")
        for _, e in ev.iterrows():
            mark = {"+": "▲", "-": "▼"}.get(e["tone"], "·")
            L.append(f"- {e['date'].date()} {mark} {e['event']}")
        L.append("")

    L.append("### 다음 체크포인트")
    for d, what in j["checkpoints"]:
        L.append(f"- {d.date()}: {what}")
    L.append("")
    return "\n".join(L)


def compare_table(res: List[tuple]) -> str:
    L = ["| 종목 | 오너 매수(2년) | 주주환원 | 승계 단계 | 판단 |", "|---|---|---|---|---|"]
    for f, j in res:
        r = j["returns"]
        ret = "강화" if r["strong"] else ("축소" if r.get("dps_cut") else "보통")
        L.append(f"| {f.name} | {j['owner']['buy_cnt']}건 | {ret} | {j['succession']['stage']} | {j['verdict']} |")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser(description="오너 이해관계 스토리 분석")
    ap.add_argument("codes", nargs="+", help="6자리 종목코드")
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--dart-key", default=os.environ.get("DART_API_KEY"))
    ap.add_argument("--cache-dir", default=os.path.join(HERE, "dart_cache"))
    ap.add_argument("--fixture-dir", help="오프라인: DART 응답 JSON 폴더 (캐시와 같은 형식)")
    ap.add_argument("--csv-dir", help="오프라인 일봉 CSV 폴더")
    ap.add_argument("--today", help="기준일 (YYYY-MM-DD), 기본 오늘")
    ap.add_argument("--out-dir", default="stories")
    a = ap.parse_args()

    offline = bool(a.fixture_dir)
    if not offline and not a.dart_key:
        sys.exit("DART_API_KEY 환경변수 또는 --dart-key 가 필요합니다 (opendart.fss.or.kr 무료 발급).")
    dart = Dart(a.dart_key, a.fixture_dir or a.cache_dir, offline)
    today = datetime.strptime(a.today, "%Y-%m-%d").date() if a.today else date.today()
    os.makedirs(a.out_dir, exist_ok=True)

    results = []
    for code in a.codes:
        f = collect(dart, code, a.years, today, a.csv_dir)
        if f is None:
            print(f"{code}: DART 기업코드를 찾지 못했습니다.", file=sys.stderr)
            continue
        j = judge(f, today, a.years)
        story = write_story(f, j, a.years)
        with open(os.path.join(a.out_dir, f"{code}_story.md"), "w", encoding="utf-8") as fp:
            fp.write(story)
        print(story)
        results.append((f, j))
    if results:
        table = compare_table(results)
        print("## 한눈에 보기\n\n" + table)
        with open(os.path.join(a.out_dir, "summary.md"), "w", encoding="utf-8") as fp:
            fp.write(f"# 오너 이해관계 스토리 ({today})\n\n{table}\n\n"
                     + "\n\n".join(write_story(f, j, a.years) for f, j in results))
        print(f"\n저장: {a.out_dir}/summary.md 및 종목별 *_story.md")
    print("\n※ 공시 기반 자동 판정이며 뉴스·비공개 사정은 반영하지 않습니다. 투자 판단은 본인 책임입니다.")


if __name__ == "__main__":
    main()
