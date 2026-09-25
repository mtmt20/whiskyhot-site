#!/usr/bin/env python3
"""
장기 매집 흔적 분석기 (Long-term Accumulation Detector)

거래는 적지만 누군가 가격선을 지키며 주기적으로 사 모으는 종목을 찾는다.
종목 하나를 5년 단위로 해부하거나(연도별 표 + 차트), 시장 전체를 스캔해 순위를 낸다.

보는 흔적
  1. 가격선 방어   : 60일 바닥선 근처까지 밀려도 종가는 지켜지는 비율, 바닥선이 계단식으로 올라가는지
  2. 거래량 비대칭 : 오른 날 거래량 > 내린 날 거래량, 내릴 때 거래량이 마름
  3. 누적 거래량선 : 가격은 제자리인데 OBV·AD(Accumulation/Distribution)선만 오름
  4. 주기적 매수   : 평소의 3배 넘는 거래량이 터진 날의 간격 규칙성, 터진 뒤 가격이 유지되면 '흡수형'
                    윗꼬리 길거나 이후 급락하면 '털기형'
  5. 종가 관리     : 장중 범위 상단에서 마감하는 날의 비율
  6. 투자자 수급   : 외국인·기관·연기금·투신·사모·기타법인·개인의 누적 순매수와 월별 일관성 (pykrx)
  7. 내부자 매수   : 임원·주요주주 소유보고의 증감 (DART 오픈API)

사용 예
  python accumulation.py 294630 026960               # 서남, 동서 5년 분석 (보고서+차트)
  python accumulation.py 026960 --years 5 --flows    # KRX 수급 포함 (KRX_ID/KRX_PW 필요)
  DART_API_KEY=키 python accumulation.py 026960      # 내부자 매수 포함
  python accumulation.py --scan-all --max-value 50   # 전 종목 스캔 (일평균 거래대금 50억 이하)
  python accumulation.py --csv-dir ./ohlcv A B       # 오프라인 CSV (Date,Open,High,Low,Close,Volume)
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import zipfile
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from flows import fetch_flows, load_flows  # noqa: E402

KNOWN_NAMES = {"294630": "서남", "026960": "동서"}
INVESTORS = [("foreign", "외국인"), ("institution", "기관계"), ("pension", "연기금"), ("trust", "투신"),
             ("private_eq", "사모"), ("fin_invest", "금융투자"), ("other_corp", "기타법인"),
             ("individual", "개인")]


# --------------------------------------------------------------------------- #
# 데이터
# --------------------------------------------------------------------------- #
def load_daily(code: str, start: str, end: str, cache_dir: str,
               offline: bool = False) -> Optional[pd.DataFrame]:
    """pykrx → yfinance(.KS/.KQ) 순서로 시도, 결과는 cache_dir/<code>.csv 로 캐시.
    offline=True면 cache_dir의 CSV만 그대로 쓴다."""
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"{code}.csv")
    if os.path.exists(path):
        df = pd.read_csv(path, parse_dates=["Date"]).set_index("Date")
        if offline:
            return df
        if df.index[0] <= pd.Timestamp(start) + timedelta(days=10) and \
                df.index[-1] >= pd.Timestamp(end) - timedelta(days=4):
            return df
    df = None
    try:
        from pykrx import stock
        raw = stock.get_market_ohlcv(start.replace("-", ""), end.replace("-", ""), code)
        if raw is not None and not raw.empty:
            df = raw.rename(columns={"시가": "Open", "고가": "High", "저가": "Low",
                                     "종가": "Close", "거래량": "Volume"})[["Open", "High", "Low", "Close", "Volume"]]
    except Exception as ex:
        print(f"[pykrx 실패] {code}: {type(ex).__name__}", file=sys.stderr)
    if df is None or df.empty:
        try:
            import yfinance as yf
            for suf in (".KS", ".KQ"):
                raw = yf.download(code + suf, start=start, end=end, auto_adjust=True, progress=False)
                if raw is not None and not raw.empty:
                    if isinstance(raw.columns, pd.MultiIndex):
                        raw.columns = raw.columns.get_level_values(0)
                    df = raw[["Open", "High", "Low", "Close", "Volume"]]
                    break
        except Exception as ex:
            print(f"[yfinance 실패] {code}: {type(ex).__name__}", file=sys.stderr)
    if offline or df is None or df.empty:
        return None
    df = df[(df["Volume"] > 0) & (df["Close"] > 0)].copy()   # 거래정지일 제거
    df.index.name = "Date"
    df.to_csv(path)
    return df


def krx_ticker(code: str) -> str:
    """6자리 코드 → 야후식 티커 (.KS 코스피 / .KQ 코스닥)."""
    try:
        from pykrx import stock
        today = datetime.now().strftime("%Y%m%d")
        if code in stock.get_market_ticker_list(today, "KOSDAQ"):
            return code + ".KQ"
    except Exception:
        if code in ("294630",):
            return code + ".KQ"
    return code + ".KS"


def stock_name(code: str) -> str:
    if code in KNOWN_NAMES:
        return KNOWN_NAMES[code]
    try:
        from pykrx import stock
        return stock.get_market_ticker_name(code) or code
    except Exception:
        return code


def dart_insider(code: str, key: str, cache_dir: str) -> Optional[pd.DataFrame]:
    """DART 임원·주요주주 소유보고(elestock). 보고일, 보고자, 증감 주식수."""
    import requests
    os.makedirs(cache_dir, exist_ok=True)
    cc_path = os.path.join(cache_dir, "dart_corpcode.json")
    if not os.path.exists(cc_path):
        r = requests.get("https://opendart.fss.or.kr/api/corpCode.xml", params={"crtfc_key": key}, timeout=60)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        import xml.etree.ElementTree as ET
        root = ET.fromstring(z.read(z.namelist()[0]))
        m = {e.findtext("stock_code").strip(): e.findtext("corp_code")
             for e in root.iter("list") if (e.findtext("stock_code") or "").strip()}
        with open(cc_path, "w") as f:
            json.dump(m, f)
    with open(cc_path) as f:
        corp = json.load(f).get(code)
    if not corp:
        return None
    r = requests.get("https://opendart.fss.or.kr/api/elestock.json",
                     params={"crtfc_key": key, "corp_code": corp}, timeout=60).json()
    if r.get("status") != "000":
        print(f"[DART] {code}: {r.get('message')}", file=sys.stderr)
        return None
    df = pd.DataFrame(r["list"])
    num = lambda s: pd.to_numeric(s.astype(str).str.replace(",", ""), errors="coerce")  # noqa: E731
    out = pd.DataFrame({"date": pd.to_datetime(df["rcept_dt"]), "who": df["repror"],
                        "change": num(df["sp_stock_lmp_irds_cnt"]), "held": num(df["sp_stock_lmp_cnt"])})
    return out.sort_values("date")


# --------------------------------------------------------------------------- #
# 지표
# --------------------------------------------------------------------------- #
def compute_series(df: pd.DataFrame) -> pd.DataFrame:
    d = df[["Open", "High", "Low", "Close", "Volume"]].astype(float).copy()
    o, h, l, c, v = d["Open"], d["High"], d["Low"], d["Close"], d["Volume"]
    rng = (h - l).replace(0, np.nan)
    d["value"] = c * v
    d["clv"] = (((c - l) - (h - c)) / rng).fillna(0)
    d["up"] = c > c.shift()
    d["down"] = c < c.shift()
    d["obv"] = (np.sign(c.diff()).fillna(0) * v).cumsum()
    d["ad"] = (d["clv"] * v).cumsum()
    # 60일 바닥선: 과거 60일 저가의 10% 분위 (당일 제외)
    d["floor"] = l.shift(1).rolling(60, min_periods=40).quantile(0.10)
    d["touch"] = l <= d["floor"] * 1.02
    d["defended"] = d["touch"] & (c > d["floor"])
    d["broken"] = c < d["floor"] * 0.97
    # 거래량 급증일 분류
    med = v.shift(1).rolling(60, min_periods=20).median()
    d["spike"] = v > 3 * med
    fwd5 = c.shift(-5) / c - 1
    fwd20 = c.shift(-20) / c - 1
    wick = (h - pd.concat([o, c], axis=1).max(axis=1)) / rng
    d["absorb"] = d["spike"] & (d["clv"] > 0) & (fwd5 > -0.03) & (fwd20 > -0.08)
    d["dump"] = d["spike"] & ((wick.fillna(0) > 0.5) | (fwd20 < -0.10))
    d["range120"] = (h.rolling(120).max() - l.rolling(120).min()) / c.rolling(120).mean()
    return d


def spike_regularity(d: pd.DataFrame) -> dict:
    idx = np.where(d["spike"].values)[0]
    if len(idx) < 4:
        return {"n": int(len(idx)), "median_gap": np.nan, "cv": np.nan}
    gaps = np.diff(idx)
    return {"n": int(len(idx)), "median_gap": float(np.median(gaps)),
            "cv": float(np.std(gaps) / np.mean(gaps))}


def window_signals(d: pd.DataFrame, fl: Optional[pd.DataFrame], ins: Optional[pd.DataFrame]) -> dict:
    """주어진 구간(보통 최근 1년 또는 전체)의 매집 신호와 0~1 점수."""
    v, c = d["Volume"], d["Close"]
    up_v, dn_v = v[d["up"]].sum(), v[d["down"]].sum()
    s = {
        "수익률%": (c.iloc[-1] / c.iloc[0] - 1) * 100,
        "일평균거래대금(억)": d["value"].mean() / 1e8,
        "상승/하락거래량비": up_v / dn_v if dn_v > 0 else np.nan,
        "하락일/상승일 평균거래량": v[d["down"]].mean() / v[d["up"]].mean() if d["up"].any() else np.nan,
        "OBV순증%": (d["obv"].iloc[-1] - d["obv"].iloc[0]) / v.sum() * 100,
        "AD순증%": (d["ad"].iloc[-1] - d["ad"].iloc[0]) / v.sum() * 100,
        "바닥 지지율%": d["defended"].sum() / max(d["touch"].sum(), 1) * 100,
        "바닥 이탈일%": d["broken"].mean() * 100,
        "바닥선 변화%": (d["floor"].dropna().iloc[-1] / d["floor"].dropna().iloc[0] - 1) * 100
        if d["floor"].notna().sum() > 1 else np.nan,
        "120일 박스폭%": d["range120"].median() * 100,
        "종가 상단마감%": (d["clv"] > 0).mean() * 100,
        "거래량급증": int(d["spike"].sum()),
        "흡수형": int(d["absorb"].sum()),
        "털기형": int(d["dump"].sum()),
    }
    reg = spike_regularity(d)
    s["급증 간격(중앙값,일)"] = reg["median_gap"]
    s["급증 간격 불규칙도"] = reg["cv"]

    # 수급
    flow_best = None
    if fl is not None and not fl.empty:
        f = fl.reindex(d.index).fillna(0)
        tv = d["value"].sum()
        for col, ko in INVESTORS:
            if col in f:
                net = f[col].sum()
                monthly = f[col].resample("ME").sum()
                cons = (monthly > 0).mean() if len(monthly) else np.nan
                s[f"{ko} 순매수(억)"] = net / 1e8
                s[f"{ko} 순매수월%"] = cons * 100
                if col != "individual" and net > 0:
                    score = cons * min(net / tv * 20, 1) if tv > 0 else 0
                    if flow_best is None or score > flow_best[1]:
                        flow_best = (ko, score)
        s["주 매집 주체"] = flow_best[0] if flow_best else "-"

    # 내부자
    if ins is not None and not ins.empty:
        w = ins[(ins["date"] >= d.index[0]) & (ins["date"] <= d.index[-1])]
        s["내부자 증가보고"] = int((w["change"] > 0).sum())
        s["내부자 감소보고"] = int((w["change"] < 0).sum())
        s["내부자 순증주식"] = float(w["change"].sum())

    # 점수 (0~1)
    clip = lambda x: float(np.clip(x, 0, 1)) if pd.notna(x) else 0.0  # noqa: E731
    sub = {
        "거래량 비대칭": clip((s["상승/하락거래량비"] - 1.0) / 0.4),
        "내릴 때 마름": clip((1 - s["하락일/상승일 평균거래량"]) / 0.3),
        "OBV·AD 상승": clip((s["OBV순증%"] + s["AD순증%"]) / 2 / 15),
        "가격선 방어": clip((s["바닥 지지율%"] - 40) / 40) * clip(1 - s["바닥 이탈일%"] / 15),
        "바닥 계단상승": clip(s["바닥선 변화%"] / 20),
        "종가 관리": clip((s["종가 상단마감%"] - 48) / 10),
    }
    if s["거래량급증"] >= 3:   # 급증이 너무 적으면 판단 보류
        sub["흡수형 급증"] = clip((s["흡수형"] - s["털기형"]) / s["거래량급증"] * 2)
    if flow_best:
        sub["수급 일관성"] = clip(flow_best[1] * 1.5)
    if "내부자 순증주식" in s:
        sub["내부자 매수"] = 1.0 if s["내부자 순증주식"] > 0 and s["내부자 증가보고"] >= 2 else \
            (0.5 if s["내부자 순증주식"] > 0 else 0.0)
    weights = {"거래량 비대칭": 1.2, "내릴 때 마름": 0.8, "OBV·AD 상승": 1.2, "가격선 방어": 1.2,
               "바닥 계단상승": 0.8, "흡수형 급증": 1.0, "종가 관리": 0.6, "수급 일관성": 1.5, "내부자 매수": 1.5}
    tot = sum(weights[k] for k in sub)
    score = sum(sub[k] * weights[k] for k in sub) / tot * 100
    penalty = min(s["털기형"] / max(s["거래량급증"], 1), 1) * 20
    s["매집점수"] = round(max(score - penalty, 0), 1)
    s["_sub"] = sub
    return s


def verdict(score: float) -> str:
    if score >= 65:
        return "매집 흔적 강함"
    if score >= 50:
        return "매집 흔적 있음"
    if score >= 35:
        return "중립"
    return "매집 흔적 약함 또는 분산(매도) 우위"


def analyze(code: str, df: pd.DataFrame, fl, ins, years: int) -> dict:
    d = compute_series(df)
    start = d.index[-1] - pd.DateOffset(years=years)
    d = d[d.index >= start]
    yearly = []
    for y, g in d.groupby(d.index.year):
        if len(g) < 40:
            continue
        s = window_signals(g, fl, ins)
        s["연도"] = y
        yearly.append(s)
    last = window_signals(d.iloc[-250:], fl, ins)
    full = window_signals(d, fl, ins)
    base = d.iloc[-310:-60] if len(d) >= 310 else None        # 직전 60일 이전 1년 (비교 기준)
    recent = window_signals(d.iloc[-60:], fl, ins)
    return {"code": code, "d": d, "yearly": yearly, "last": last, "full": full, "years": years,
            "recent60": recent, "base": window_signals(base, fl, ins) if base is not None else None}


# --------------------------------------------------------------------------- #
# 출력
# --------------------------------------------------------------------------- #
YEAR_COLS = ["연도", "수익률%", "일평균거래대금(억)", "상승/하락거래량비", "OBV순증%", "AD순증%",
             "바닥 지지율%", "바닥선 변화%", "종가 상단마감%", "거래량급증", "흡수형", "털기형", "매집점수"]


def print_report(name: str, res: dict) -> None:
    code = res["code"]
    d = res["d"]
    print("=" * 96)
    print(f" {name}({code})  {d.index[0].date()} ~ {d.index[-1].date()}  "
          f"최근가 {d['Close'].iloc[-1]:,.0f}  기간수익률 {res['full']['수익률%']:+.1f}%")
    print("=" * 96)
    yt = pd.DataFrame(res["yearly"])
    extra = [c for c in yt.columns if c.endswith("순매수(억)") and c not in YEAR_COLS]
    extra += [c for c in ("내부자 순증주식",) if c in yt.columns]
    with pd.option_context("display.width", 220, "display.max_columns", 40, "display.float_format", "{:,.1f}".format):
        print(yt[[c for c in YEAR_COLS if c in yt] + extra].to_string(index=False))
    for label, s in (("최근 1년", res["last"]), (f"전체 {res['years']}년", res["full"])):
        print(f"\n[{label}] 매집점수 {s['매집점수']} → {verdict(s['매집점수'])}")
        print("  세부: " + ", ".join(f"{k} {v*100:.0f}" for k, v in s["_sub"].items()))
        print(f"  거래량 급증 {s['거래량급증']}회 (흡수형 {s['흡수형']}, 털기형 {s['털기형']}), "
              f"간격 중앙값 {s['급증 간격(중앙값,일)']:.0f}일, 불규칙도 {s['급증 간격 불규칙도']:.2f} "
              f"(0.6 미만이면 주기적)" if pd.notna(s["급증 간격(중앙값,일)"]) else
              f"  거래량 급증 {s['거래량급증']}회")
        if "주 매집 주체" in s:
            parts = [f"{ko} {s[f'{ko} 순매수(억)']:+,.0f}억(순매수월 {s[f'{ko} 순매수월%']:.0f}%)"
                     for _, ko in INVESTORS if f"{ko} 순매수(억)" in s]
            print("  수급: " + ", ".join(parts))
            print(f"  주 매집 주체: {s['주 매집 주체']}")
        if "내부자 순증주식" in s:
            print(f"  내부자 보고: 증가 {s['내부자 증가보고']}건, 감소 {s['내부자 감소보고']}건, "
                  f"순증 {s['내부자 순증주식']:+,.0f}주")
    r60, b = res["recent60"], res["base"]
    if b is not None:
        keys = ["일평균거래대금(억)", "상승/하락거래량비", "하락일/상승일 평균거래량", "OBV순증%", "AD순증%",
                "종가 상단마감%", "거래량급증", "흡수형"]
        keys += [k for k in r60 if k.endswith("순매수(억)")]
        print(f"\n[직전 60일 vs 그 이전 1년]  매집점수 {r60['매집점수']} vs {b['매집점수']}")
        for k in keys:
            if k in b:
                print(f"  {k:<18} {r60[k]:>10,.2f}   |   {b[k]:>10,.2f}")
    top = d[d["spike"]].tail(8)
    if not top.empty:
        print("\n  최근 거래량 급증일: " + ", ".join(
            f"{i.date()}({'흡수' if r['absorb'] else '털기' if r['dump'] else '중립'})" for i, r in top.iterrows()))


def save_chart(name: str, res: dict, fl, ins, path: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    d = res["d"]
    has_flow = fl is not None and not fl.empty
    n = 4 if has_flow else 3
    fig, ax = plt.subplots(n, 1, figsize=(13, 3.2 * n), sharex=True,
                           gridspec_kw={"height_ratios": [3, 1.5, 1.2] + ([1.8] if has_flow else [])})
    a = ax[0]
    a.plot(d.index, d["Close"], lw=1.1, color="#1f3b73", label="Close")
    a.plot(d.index, d["floor"], lw=1, color="#c77d00", ls="--", label="60d floor")
    ab, dm = d[d["absorb"]], d[d["dump"]]
    a.scatter(ab.index, ab["Close"], s=22, color="#1a9850", zorder=3, label="Absorb spike")
    a.scatter(dm.index, dm["Close"], s=22, color="#d73027", zorder=3, label="Dump spike")
    if ins is not None and not ins.empty:
        w = ins[(ins["date"] >= d.index[0])]
        for _, r in w.iterrows():
            col = "#1a9850" if r["change"] > 0 else "#d73027"
            a.axvline(r["date"], color=col, alpha=0.25, lw=1)
    a.set_title(f"{res['code']}  accumulation score (1y) {res['last']['매집점수']}  / (all) {res['full']['매집점수']}")
    a.legend(loc="upper left", fontsize=8)
    a.grid(alpha=0.2)
    b = ax[1]
    tv = d["Volume"].cumsum()
    b.plot(d.index, (d["obv"] - d["obv"].iloc[0]) / tv.iloc[-1] * 100, label="OBV (% of total vol)", color="#6a3d9a")
    b.plot(d.index, (d["ad"] - d["ad"].iloc[0]) / tv.iloc[-1] * 100, label="A/D (% of total vol)", color="#b15928")
    b.axhline(0, color="gray", lw=0.6)
    b.legend(loc="upper left", fontsize=8)
    b.grid(alpha=0.2)
    cvol = ax[2]
    colors = np.where(d["up"], "#d73027", np.where(d["down"], "#4575b4", "#999999"))
    cvol.bar(d.index, d["Volume"], color=colors, width=1.0)
    cvol.set_ylabel("Volume")
    cvol.grid(alpha=0.2)
    if has_flow:
        f = fl.reindex(d.index).fillna(0).cumsum() / 1e8
        en = {"foreign": "Foreign", "institution": "Institution", "pension": "Pension",
              "other_corp": "Other corp", "individual": "Individual", "trust": "Trust", "private_eq": "PEF"}
        for col, lbl in en.items():
            if col in f:
                ax[3].plot(f.index, f[col], lw=1, label=lbl)
        ax[3].axhline(0, color="gray", lw=0.6)
        ax[3].set_ylabel("Cum. net buy (100M KRW)")
        ax[3].legend(loc="upper left", fontsize=8, ncol=4)
        ax[3].grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


# --------------------------------------------------------------------------- #
def scan(codes: List[str], start: str, end: str, cache: str, flows: Dict[str, pd.DataFrame],
         min_value: float, max_value: float, offline: bool = False) -> pd.DataFrame:
    rows = []
    for i, code in enumerate(codes):
        df = load_daily(code, start, end, cache, offline)
        if df is None or len(df) < 300:
            continue
        d = compute_series(df).iloc[-250:]
        val = d["value"].mean() / 1e8
        if not (min_value <= val <= max_value):
            continue
        fl = flows.get(code + ".KS", flows.get(code + ".KQ", flows.get(code)))
        s = window_signals(d, fl, None)
        rows.append({"code": code, "name": stock_name(code), "매집점수": s["매집점수"],
                     "판정": verdict(s["매집점수"]), "거래대금(억)": round(val, 1),
                     "1년수익률%": round(s["수익률%"], 1), "거래량비": round(s["상승/하락거래량비"], 2),
                     "OBV순증%": round(s["OBV순증%"], 1), "바닥지지율%": round(s["바닥 지지율%"], 0),
                     "흡수형": s["흡수형"], "털기형": s["털기형"], "주체": s.get("주 매집 주체", "-")})
        if (i + 1) % 100 == 0:
            print(f"  {i+1}/{len(codes)} 종목 처리", file=sys.stderr)
    return pd.DataFrame(rows).sort_values("매집점수", ascending=False) if rows else pd.DataFrame()


def main() -> None:
    ap = argparse.ArgumentParser(description="장기 매집 흔적 분석")
    ap.add_argument("codes", nargs="*", help="6자리 종목코드 (예: 294630 026960)")
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--asof", help="이 날짜까지만 보고 분석 (예: 2023-07-21, 이벤트 직전 매집 확인용)")
    ap.add_argument("--csv-dir", help="오프라인 일봉 CSV 폴더 (<코드>.csv)")
    ap.add_argument("--cache-dir", default=os.path.join(HERE, "ohlcv_cache"))
    ap.add_argument("--flows", action="store_true", help="KRX 투자자별 수급 받기 (KRX_ID/KRX_PW 필요)")
    ap.add_argument("--flows-dir", default=os.path.join(HERE, "flows_cache"))
    ap.add_argument("--dart-key", default=os.environ.get("DART_API_KEY"), help="DART 오픈API 키")
    ap.add_argument("--insider-csv", help="내부자 매매 CSV (code,date,who,change) - DART 대신 수동 입력용")
    ap.add_argument("--out-dir", default="accumulation_reports")
    ap.add_argument("--scan-all", action="store_true", help="코스피·코스닥 전 종목 스캔 (pykrx)")
    ap.add_argument("--scan-file", help="스캔할 종목코드 목록 파일")
    ap.add_argument("--min-value", type=float, default=1.0, help="스캔: 일평균 거래대금 하한(억)")
    ap.add_argument("--max-value", type=float, default=50.0, help="스캔: 일평균 거래대금 상한(억)")
    a = ap.parse_args()

    end_dt = pd.Timestamp(a.asof) if a.asof else pd.Timestamp(datetime.now().date())
    end = end_dt.strftime("%Y-%m-%d")
    start = (end_dt - timedelta(days=365 * a.years + 120)).strftime("%Y-%m-%d")
    cache = a.csv_dir or a.cache_dir
    os.makedirs(a.out_dir, exist_ok=True)

    if a.scan_all or a.scan_file:
        if a.scan_file:
            codes = [x.strip().split(",")[0] for x in open(a.scan_file, encoding="utf-8") if x.strip()]
        else:
            from pykrx import stock
            today = datetime.now().strftime("%Y%m%d")
            codes = stock.get_market_ticker_list(today, "KOSPI") + stock.get_market_ticker_list(today, "KOSDAQ")
        flows = load_flows(a.flows_dir)
        print(f"{len(codes)}개 종목 스캔 (거래대금 {a.min_value}~{a.max_value}억)...", file=sys.stderr)
        res = scan(codes, start, end, cache, flows, a.min_value, a.max_value, bool(a.csv_dir))
        out = os.path.join(a.out_dir, "scan_result.csv")
        res.to_csv(out, index=False, encoding="utf-8-sig")
        with pd.option_context("display.width", 220, "display.max_columns", 20):
            print(res.head(30).to_string(index=False))
        print(f"\n전체 결과: {out}")
        return

    if not a.codes:
        ap.error("종목코드를 넣거나 --scan-all 을 쓰세요.")
    if a.flows:
        fetch_flows([krx_ticker(c) for c in a.codes], start, end, a.flows_dir)
    flows = load_flows(a.flows_dir)
    for code in a.codes:
        df = load_daily(code, start, end, cache, bool(a.csv_dir))
        if df is None:
            print(f"{code}: 데이터를 불러오지 못했습니다.", file=sys.stderr)
            continue
        if a.asof:
            df = df[df.index <= end_dt]
        fl = flows.get(code + ".KS", flows.get(code + ".KQ", flows.get(code)))
        ins = None
        if a.insider_csv:
            t = pd.read_csv(a.insider_csv, dtype={"code": str}, parse_dates=["date"])
            ins = t[t["code"] == code][["date", "who", "change"]].sort_values("date")
        elif a.dart_key:
            try:
                ins = dart_insider(code, a.dart_key, a.cache_dir)
            except Exception as ex:
                print(f"[DART 실패] {code}: {type(ex).__name__}", file=sys.stderr)
        name = stock_name(code)
        res = analyze(code, df, fl, ins, a.years)
        print_report(name, res)
        png = os.path.join(a.out_dir, f"{code}_accumulation.png")
        save_chart(name, res, fl, ins, png)
        pd.DataFrame([{k: v for k, v in y.items() if k != "_sub"} for y in res["yearly"]]).to_csv(
            os.path.join(a.out_dir, f"{code}_yearly.csv"), index=False, encoding="utf-8-sig")
        print(f"\n  차트: {png}\n")
    print("※ 매집 흔적은 확률적 정황이며 세력의 존재를 증명하지 않습니다. 투자 판단은 본인 책임입니다.")


if __name__ == "__main__":
    main()
