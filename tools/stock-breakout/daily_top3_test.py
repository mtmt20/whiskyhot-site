#!/usr/bin/env python3
"""
날마다 3종목 전고점 돌파 테스트 (Daily Top-3 Breakout Walk-Forward Test)

실존 매매기법을 점수화해 매일 상위 3종목을 고르고, 2~3거래일 안에 전고점을
돌파했는지 과거 전 구간에 걸쳐 하루씩 검증한다. 미래 데이터는 쓰지 않는다
(t일 종가까지로 선정 → t+1일 진입).

적용한 매매기법
  - Minervini 트렌드 템플릿 + VCP(변동성 수축, 거래량 고갈)
  - O'Neil CAN SLIM 기술적 요소: 상대강도(RS) 등급, 상승/하락 거래량비, 포켓피벗
  - Wyckoff 매집 판별: OBV 기울기, 가격 횡보 중 OBV 상승(다이버전스), CMF(자금흐름)
  - Darvas 박스: 좁은 박스 상단 근접
  - 테마/신기술 모멘텀: AI반도체·HBM, 2차전지, 방산, 조선, 원전, 로봇, 바이오 등
    테마별 20일 상대강도 순위 → 주도 테마 가점
  - 시장 추세 필터: 유니버스 동일가중 지수가 20일선 위일 때만 매매(옵션)

매매시간 고정 (거래량이 몰리는 시간)
  - 일봉 모드: t+1일 시가(09:00 동시호가~장초반) 진입, 미돌파 시 N일째 종가(15:20 동시호가) 청산
  - 분봉 모드(--intraday): 60분봉에서 종목별 평균 거래량이 가장 큰 시간대를 자동 탐지해
    그 시간 봉의 평균가((H+L+C)/3)로 진입·청산 시각을 고정

사용 예
  python daily_top3_test.py                          # 한국 유니버스, 5년 백테스트
  python daily_top3_test.py --strategy combo --days 3 --picks 3
  python daily_top3_test.py --intraday               # 60분봉(최근 730일) 기반
  python daily_top3_test.py --journal picks.csv      # 오늘 3종목을 저널에 기록 + 지난 픽 채점
  python daily_top3_test.py --csv-dir ./ohlcv        # 오프라인 CSV
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from breakout_finder import (  # noqa: E402
    KR_UNIVERSE, US_UNIVERSE, load_from_csv_dir, load_from_yfinance, rsi,
)

# --------------------------------------------------------------------------- #
# 테마 / 신기술 분류 (필요 시 수정)
# --------------------------------------------------------------------------- #
THEMES: Dict[str, List[str]] = {
    "AI반도체·HBM": ["005930.KS", "000660.KS", "403870.KQ", "058470.KQ", "039030.KQ",
                     "009150.KS", "011070.KS", "NVDA", "AMD", "AVGO", "MU", "AMAT", "LRCX", "QCOM"],
    "2차전지": ["373220.KS", "006400.KS", "051910.KS", "003670.KS", "247540.KQ",
               "086520.KQ", "066970.KQ", "005070.KS", "096770.KS", "TSLA"],
    "방산·우주": ["012450.KS", "064350.KS", "047050.KS", "GE"],
    "조선·해운": ["329180.KS", "042660.KS", "009540.KS", "267250.KS", "011200.KS"],
    "원전·전력": ["015760.KS", "034730.KS"],
    "바이오": ["207940.KS", "068270.KS", "196170.KQ", "028300.KQ", "000100.KS",
              "302440.KS", "326030.KS", "145020.KQ", "214150.KQ", "LLY", "ABBV", "MRK"],
    "AI소프트웨어·플랫폼": ["035420.KS", "035720.KS", "018260.KS", "MSFT", "GOOGL", "META",
                         "PLTR", "NOW", "CRM", "ORCL", "ADBE", "INTU"],
    "금융·밸류업": ["105560.KS", "055550.KS", "086790.KS", "316140.KS", "138040.KS",
                 "024110.KS", "032830.KS", "000810.KS", "JPM", "BAC", "V", "MA"],
    "K컬처·게임": ["041510.KQ", "035900.KQ", "263750.KQ", "293490.KQ", "112040.KQ", "NFLX"],
}
TICKER_THEMES: Dict[str, List[str]] = {}
for _th, _ts in THEMES.items():
    for _t in _ts:
        TICKER_THEMES.setdefault(_t, []).append(_th)

# 전략 프리셋: 각 요소 가중치
STRATEGIES: Dict[str, Dict[str, float]] = {
    "minervini": {"trend": 0.40, "vcp": 0.35, "rs": 0.15, "prox": 0.10},
    "oneil":     {"rs": 0.40, "accum": 0.30, "trend": 0.15, "prox": 0.15},
    "wyckoff":   {"accum": 0.60, "vcp": 0.20, "prox": 0.20},
    "darvas":    {"box": 0.60, "rs": 0.20, "prox": 0.20},
    "theme":     {"theme": 0.50, "rs": 0.30, "prox": 0.20},
    "combo":     {"trend": 0.15, "vcp": 0.15, "rs": 0.15, "accum": 0.20,
                  "box": 0.05, "theme": 0.15, "prox": 0.15},
}


@dataclass
class TestConfig:
    lookback: int = 120     # 전고점 산정 기간
    gap: int = 5            # 전고점 산정에서 제외할 최근 일수
    near: float = 0.05      # 전고점 아래 이 비율 이내만 후보
    days: int = 3           # 돌파 판정 기간(2~3일)
    picks: int = 3          # 하루 선정 종목 수
    margin: float = 0.0     # 돌파 인정 여유(0.005 = 전고점 +0.5%)
    regime_filter: bool = True
    start_offset: int = 260  # 지표 준비 기간(52주)


# --------------------------------------------------------------------------- #
# 종목별 지표
# --------------------------------------------------------------------------- #
def indicators(df: pd.DataFrame, cfg: TestConfig) -> pd.DataFrame:
    d = pd.DataFrame(index=df.index)
    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    v = df["Volume"].astype(float)
    d["Open"], d["High"], d["Low"], d["Close"], d["Volume"] = o, h, l, c, v

    # 전고점
    d["prior_high"] = h.shift(cfg.gap).rolling(cfg.lookback, min_periods=cfg.lookback // 2).max()
    d["dist"] = (d["prior_high"] - c) / d["prior_high"]

    # Minervini 트렌드 템플릿 (RS 조건 제외한 7개 조건 충족 비율)
    ma50, ma150, ma200 = c.rolling(50).mean(), c.rolling(150).mean(), c.rolling(200).mean()
    hi252, lo252 = h.rolling(252, min_periods=200).max(), l.rolling(252, min_periods=200).min()
    conds = [
        c > ma150, c > ma200, ma150 > ma200, ma200 > ma200.shift(20),
        (ma50 > ma150) & (ma50 > ma200), c > ma50,
        (c >= lo252 * 1.30) & (c >= hi252 * 0.75),
    ]
    d["trend"] = sum(x.astype(float) for x in conds) / len(conds)
    d.loc[ma200.isna(), "trend"] = np.nan

    # RS 원점수 (IBD 방식 가중 수익률) → 날짜별 백분위는 패널에서 계산
    d["rs_raw"] = (0.4 * c.pct_change(63) + 0.2 * c.pct_change(126)
                   + 0.2 * c.pct_change(189) + 0.2 * c.pct_change(252))
    d["ret20"] = c.pct_change(20)

    # VCP: 단기 변동성/장기 변동성(낮을수록 수축), 거래량 고갈
    r = c.pct_change()
    d["contraction"] = r.rolling(10).std() / r.rolling(50).std()
    d["range10"] = (h.rolling(10).max() - l.rolling(10).min()) / c
    d["vol_dryup"] = v.rolling(5).mean() / v.rolling(50).mean()

    # 매집(Wyckoff/O'Neil): 상승·하락 거래량비, OBV 기울기, CMF, 포켓피벗
    up, dn = (c > c.shift()), (c < c.shift())
    d["ud_ratio"] = (v.where(up, 0).rolling(50).sum() / v.where(dn, 0).rolling(50).sum().replace(0, np.nan))
    obv = (np.sign(c.diff()).fillna(0) * v).cumsum()
    d["obv_slope"] = (obv - obv.shift(20)) / v.rolling(20).sum().replace(0, np.nan)
    # 가격은 횡보(ret20 작음)인데 OBV 상승 → 매집 다이버전스
    d["obv_div"] = d["obv_slope"] - d["ret20"].abs() * 2
    mfm = ((c - l) - (h - c)) / (h - l).replace(0, np.nan)
    d["cmf"] = (mfm.fillna(0) * v).rolling(20).sum() / v.rolling(20).sum().replace(0, np.nan)
    max_down_vol10 = v.where(dn, 0).shift(1).rolling(10).max()
    pp = (up & (v > max_down_vol10)).astype(float)
    d["pocket_pivots"] = pp.rolling(10).sum()

    # Darvas 박스: 20일 박스 폭이 좁고 상단 근처
    hi20, lo20 = h.rolling(20).max(), l.rolling(20).min()
    box_w = (hi20 - lo20) / hi20
    d["box"] = ((c - lo20) / (hi20 - lo20).replace(0, np.nan)).fillna(0.5) * (box_w < 0.15).astype(float)

    d["rsi14"] = rsi(c, 14)
    d["liquidity"] = (c * v).rolling(20).mean()

    # 결과(라벨): t+1 ~ t+days 고가가 전고점 돌파?
    target = d["prior_high"] * (1 + cfg.margin)
    fut_highs = pd.concat([h.shift(-k) for k in range(1, cfg.days + 1)], axis=1)
    d["hit"] = (fut_highs.max(axis=1) > target).astype(float)
    d.loc[fut_highs.isna().any(axis=1), "hit"] = np.nan
    for k in (1, 2, 3):
        fh = pd.concat([h.shift(-j) for j in range(1, k + 1)], axis=1)
        d[f"hit{k}"] = (fh.max(axis=1) > target).astype(float)
        d.loc[fh.isna().any(axis=1), f"hit{k}"] = np.nan

    # 일봉 매매 시뮬: t+1 시가 진입, 목표가 도달 시 목표가 청산, 아니면 t+days 종가 청산
    entry = o.shift(-1)
    d["entry"] = entry
    exit_close = c.shift(-cfg.days)
    gap_above = entry >= target
    d["pnl"] = np.where(d["hit"] == 1,
                        np.where(gap_above, exit_close / entry - 1, target / entry - 1),
                        exit_close / entry - 1)
    d.loc[d["hit"].isna(), "pnl"] = np.nan
    d["gap_entry"] = gap_above.astype(float)
    return d


def panel(frames: Dict[str, pd.DataFrame], col: str) -> pd.DataFrame:
    return pd.DataFrame({t: f[col] for t, f in frames.items()})


def pct_rank(p: pd.DataFrame, ascending: bool = True) -> pd.DataFrame:
    """날짜별 단면 백분위(0~1)."""
    return p.rank(axis=1, pct=True, ascending=ascending)


def build_scores(frames: Dict[str, pd.DataFrame], cfg: TestConfig) -> Dict[str, pd.DataFrame]:
    P = {k: panel(frames, k) for k in [
        "dist", "trend", "rs_raw", "ret20", "contraction", "range10", "vol_dryup", "ud_ratio",
        "obv_slope", "obv_div", "cmf", "pocket_pivots", "box", "liquidity", "Close"]}

    comp: Dict[str, pd.DataFrame] = {}
    comp["trend"] = P["trend"]
    comp["rs"] = pct_rank(P["rs_raw"])
    comp["vcp"] = (pct_rank(P["contraction"], ascending=False) + pct_rank(P["range10"], ascending=False)
                   + pct_rank(P["vol_dryup"], ascending=False)) / 3
    comp["accum"] = (pct_rank(P["ud_ratio"]) + pct_rank(P["obv_slope"]) + pct_rank(P["obv_div"])
                     + pct_rank(P["cmf"]) + pct_rank(P["pocket_pivots"])) / 5
    comp["box"] = P["box"]
    comp["prox"] = pct_rank(P["dist"], ascending=False)  # 전고점에 가까울수록 높음

    # 테마 강도: 테마 평균 20일 수익률의 날짜별 순위 → 종목은 소속 테마 중 최고 점수
    ret20 = P["ret20"]
    theme_strength = {}
    for th, ts in THEMES.items():
        cols = [t for t in ts if t in ret20.columns]
        if cols:
            theme_strength[th] = ret20[cols].mean(axis=1)
    if theme_strength:
        ts_rank = pd.DataFrame(theme_strength).rank(axis=1, pct=True)
        theme_score = pd.DataFrame(0.0, index=ret20.index, columns=ret20.columns)
        for t in ret20.columns:
            ths = [th for th in TICKER_THEMES.get(t, []) if th in ts_rank.columns]
            if ths:
                theme_score[t] = ts_rank[ths].max(axis=1)
        comp["theme"] = theme_score
    else:
        comp["theme"] = pd.DataFrame(0.0, index=ret20.index, columns=ret20.columns)

    # 시장 추세: 동일가중 지수가 20일선 위
    idx = (P["Close"] / P["Close"].iloc[0]).mean(axis=1)
    regime_ok = idx > idx.rolling(20).mean()

    # 후보 조건: 전고점 아래 near 이내 + 유동성 하위 20% 제외
    liq_ok = pct_rank(P["liquidity"]) >= 0.2
    eligible = (P["dist"] >= 0) & (P["dist"] <= cfg.near) & liq_ok

    return {"comp": comp, "eligible": eligible, "regime_ok": regime_ok}


def strategy_score(comp: Dict[str, pd.DataFrame], weights: Dict[str, float]) -> pd.DataFrame:
    tot = sum(weights.values())
    s = None
    for k, w in weights.items():
        part = comp[k].fillna(0) * (w / tot)
        s = part if s is None else s + part
    return s


# --------------------------------------------------------------------------- #
# 분봉: 거래량 최대 시간대 고정 매매
# --------------------------------------------------------------------------- #
def peak_volume_hour(intra: pd.DataFrame) -> int:
    return int(intra.groupby(intra.index.hour)["Volume"].mean().idxmax())


def intraday_outcome(intra: pd.DataFrame, window_days: List[pd.Timestamp], target: float,
                     hour: int) -> Optional[dict]:
    """window_days[0](선정 다음 거래일) hour 봉에 진입, window_days 동안 돌파 여부.
    미돌파면 마지막 날 같은 hour 봉에서 청산. 분봉이 없는 날이 있으면 None."""
    have = set(intra.index.normalize())
    if any(x.normalize() not in have for x in window_days):
        return None
    day1 = intra[intra.index.normalize() == window_days[0]]
    ebar = day1[day1.index.hour == hour]
    if ebar.empty:
        return None
    ebar = ebar.iloc[0]
    entry = float((ebar["High"] + ebar["Low"] + ebar["Close"]) / 3)
    after = intra[(intra.index > ebar.name) & (intra.index.normalize() <= window_days[-1])]
    hit = bool(entry >= target or (not after.empty and after["High"].max() > target))
    lastday = intra[intra.index.normalize() == window_days[-1]]
    xbar = lastday[lastday.index.hour == hour]
    exit_px = float(((xbar["High"] + xbar["Low"] + xbar["Close"]) / 3).iloc[0]) if not xbar.empty \
        else float(lastday["Close"].iloc[-1])
    pnl = (target / entry - 1) if (hit and entry < target) else (exit_px / entry - 1)
    return {"hit": float(hit), "pnl": pnl, "entry": entry}


def load_intraday(tickers: List[str], csv_dir: Optional[str]) -> Dict[str, pd.DataFrame]:
    out = {}
    if csv_dir:
        for t in tickers:
            p = os.path.join(csv_dir, f"{t}.csv")
            if os.path.exists(p):
                out[t] = pd.read_csv(p, parse_dates=["Datetime"]).set_index("Datetime").sort_index()
        return out
    import yfinance as yf
    raw = yf.download(tickers, period="730d", interval="60m", group_by="ticker",
                      auto_adjust=True, progress=False, threads=True)
    for t in tickers:
        try:
            df = raw[t].dropna(subset=["Close"])
        except KeyError:
            continue
        if df.index.tz is not None:
            tz = "Asia/Seoul" if t.endswith((".KS", ".KQ")) else "America/New_York"
            df.index = df.index.tz_convert(tz).tz_localize(None)
        out[t] = df
    return out


# --------------------------------------------------------------------------- #
# 백테스트
# --------------------------------------------------------------------------- #
def backtest(frames: Dict[str, pd.DataFrame], cfg: TestConfig, strategies: List[str],
             intraday: Optional[Dict[str, pd.DataFrame]] = None) -> dict:
    S = build_scores(frames, cfg)
    comp, elig, regime = S["comp"], S["eligible"], S["regime_ok"]
    hit = panel(frames, "hit")
    hits = {k: panel(frames, f"hit{k}") for k in (1, 2, 3)}
    pnl = panel(frames, "pnl")
    gap = panel(frames, "gap_entry")
    ph = panel(frames, "prior_high")
    dates = elig.index[cfg.start_offset:]
    peak_hours = {t: peak_volume_hour(df) for t, df in (intraday or {}).items()}

    results = {}
    for name in strategies:
        score = strategy_score(comp, STRATEGIES[name])
        rows = []
        for dt in dates:
            if cfg.regime_filter and not bool(regime.get(dt, False)):
                continue
            e = elig.loc[dt] & hit.loc[dt].notna()
            if intraday is not None:
                e &= pd.Series({t: t in intraday for t in e.index})
            pool = e[e].index
            if len(pool) == 0:
                continue
            top = score.loc[dt, pool].sort_values(ascending=False).head(cfg.picks)
            pool_rate = float(hit.loc[dt, pool].mean())
            for t, sc in top.items():
                row = {"date": dt, "ticker": t, "score": float(sc), "pool_rate": pool_rate,
                       "pool_n": len(pool), "hit1": hits[1].loc[dt, t], "hit2": hits[2].loc[dt, t],
                       "hit3": hits[3].loc[dt, t], "hit": hit.loc[dt, t], "pnl": pnl.loc[dt, t],
                       "gap": gap.loc[dt, t]}
                if intraday is not None:
                    tgt = float(ph.loc[dt, t]) * (1 + cfg.margin)
                    pos = elig.index.get_loc(dt)
                    window = list(elig.index[pos + 1: pos + 1 + cfg.days])
                    if len(window) < cfg.days:
                        continue
                    io = intraday_outcome(intraday[t], window, tgt, peak_hours[t])
                    if io is None:
                        continue
                    row.update(hit=io["hit"], pnl=io["pnl"])
                rows.append(row)
        results[name] = pd.DataFrame(rows)
    return {"results": results, "score_frames": S, "peak_hours": peak_hours}


def summarize(results: Dict[str, pd.DataFrame], cfg: TestConfig) -> pd.DataFrame:
    rows = []
    for name, r in results.items():
        if r.empty:
            rows.append({"전략": name, "픽수": 0}); continue
        rows.append({
            "전략": name, "매매일": r["date"].nunique(), "픽수": len(r),
            "1일돌파%": round(r["hit1"].mean() * 100, 1),
            "2일돌파%": round(r["hit2"].mean() * 100, 1),
            "3일돌파%": round(r["hit3"].mean() * 100, 1),
            f"{cfg.days}일돌파%(판정)": round(r["hit"].mean() * 100, 1),
            "무작위기준%": round(r["pool_rate"].mean() * 100, 1),
            "초과%p": round((r["hit"].mean() - r["pool_rate"].mean()) * 100, 1),
            "하루3개중≥2돌파%": round((r.groupby("date")["hit"].sum() >= 2).mean() * 100, 1),
            "평균수익%": round(r["pnl"].mean() * 100, 2),
            "승률%": round((r["pnl"] > 0).mean() * 100, 1),
            "갭진입%": round(r["gap"].mean() * 100, 1),
        })
    return pd.DataFrame(rows)


def today_picks(frames, cfg: TestConfig, strategy: str, names: Dict[str, str]) -> pd.DataFrame:
    S = build_scores(frames, cfg)
    score = strategy_score(S["comp"], STRATEGIES[strategy])
    dt = S["eligible"].index[-1]
    e = S["eligible"].loc[dt]
    pool = e[e].index
    top = score.loc[dt, pool].sort_values(ascending=False).head(cfg.picks)
    out = []
    for t, sc in top.items():
        f = frames[t].loc[dt]
        out.append({"date": dt.date(), "ticker": t, "name": names.get(t, t), "score": round(sc, 3),
                    "close": round(f["Close"], 2), "prior_high": round(f["prior_high"], 2),
                    "dist%": round(f["dist"] * 100, 2), "themes": "/".join(TICKER_THEMES.get(t, [])),
                    "trend": round(f["trend"], 2), "ud_ratio": round(f["ud_ratio"], 2),
                    "cmf": round(f["cmf"], 3), "pocket_pivots": int(f["pocket_pivots"]),
                    "market_ok": bool(S["regime_ok"].loc[dt])})
    return pd.DataFrame(out)


def update_journal(path: str, picks: pd.DataFrame, frames, cfg: TestConfig) -> pd.DataFrame:
    """저널에 오늘 픽 추가, 결과가 나온 과거 픽은 채점."""
    j = pd.read_csv(path, parse_dates=["date"]) if os.path.exists(path) else pd.DataFrame()
    new = picks.copy()
    new["date"] = pd.to_datetime(new["date"])
    if not j.empty:
        new = new[~new.set_index(["date", "ticker"]).index.isin(j.set_index(["date", "ticker"]).index)]
    j = pd.concat([j, new], ignore_index=True)
    for col in ("result", "max_high", "entry_open"):
        if col not in j.columns:
            j[col] = np.nan
    j["result"] = j["result"].astype(object)
    for i, r in j.iterrows():
        if isinstance(r["result"], str) and r["result"] in ("HIT", "MISS"):
            continue
        f = frames.get(r["ticker"])
        if f is None:
            continue
        after = f[f.index > r["date"]].head(cfg.days)
        if after.empty:
            j.at[i, "result"] = "PENDING"; continue
        j.at[i, "entry_open"] = round(float(after["Open"].iloc[0]), 2)
        j.at[i, "max_high"] = round(float(after["High"].max()), 2)
        target = r["prior_high"] * (1 + cfg.margin)
        if after["High"].max() > target:
            j.at[i, "result"] = "HIT"
        elif len(after) >= cfg.days:
            j.at[i, "result"] = "MISS"
        else:
            j.at[i, "result"] = "PENDING"
    j.to_csv(path, index=False, encoding="utf-8-sig")
    return j


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description="날마다 3종목 2~3일 전고점 돌파 테스트")
    ap.add_argument("--market", choices=["kr", "us"], default="kr")
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--csv-dir")
    ap.add_argument("--period", default="5y")
    ap.add_argument("--strategy", choices=list(STRATEGIES) + ["all"], default="all")
    ap.add_argument("--days", type=int, default=3, help="돌파 판정 거래일 (2 또는 3)")
    ap.add_argument("--picks", type=int, default=3)
    ap.add_argument("--near", type=float, default=0.05)
    ap.add_argument("--margin", type=float, default=0.0)
    ap.add_argument("--no-regime", action="store_true", help="시장 추세 필터 끄기")
    ap.add_argument("--intraday", action="store_true", help="60분봉 거래량 최대 시간대로 매매시각 고정")
    ap.add_argument("--intraday-csv-dir", help="오프라인 60분봉 CSV (Datetime,Open,High,Low,Close,Volume)")
    ap.add_argument("--journal", help="오늘 픽 기록/과거 픽 채점할 CSV 경로")
    ap.add_argument("--log", default="daily_top3_log.csv", help="백테스트 일별 픽 로그 저장 경로")
    a = ap.parse_args()

    cfg = TestConfig(near=a.near, days=a.days, picks=a.picks, margin=a.margin,
                     regime_filter=not a.no_regime)
    names = dict(KR_UNIVERSE if a.market == "kr" else US_UNIVERSE)
    if a.tickers:
        names = {t: names.get(t, t) for t in a.tickers}

    if a.csv_dir:
        data = load_from_csv_dir(a.csv_dir, a.tickers)
    else:
        print(f"{len(names)}개 종목 일봉 다운로드 중...", file=sys.stderr)
        data = load_from_yfinance(list(names), a.period)
    if not data:
        sys.exit("데이터를 불러오지 못했습니다.")
    frames = {t: indicators(df, cfg) for t, df in data.items()}

    intraday = None
    if a.intraday or a.intraday_csv_dir:
        intraday = load_intraday(list(frames), a.intraday_csv_dir)
        if not intraday:
            sys.exit("분봉 데이터를 불러오지 못했습니다.")

    strategies = list(STRATEGIES) if a.strategy == "all" else [a.strategy]
    bt = backtest(frames, cfg, strategies, intraday)
    summ = summarize(bt["results"], cfg)

    mode = "60분봉 · 거래량 최대 시간대 진입/청산" if intraday else "일봉 · 시가(09:00) 진입 / 종가(15:20) 청산"
    print("=" * 100)
    print(f" 날마다 {cfg.picks}종목 · {cfg.days}거래일 내 전고점 돌파 테스트 | {mode} | "
          f"시장필터 {'ON' if cfg.regime_filter else 'OFF'} | 종목 {len(frames)}개")
    print("=" * 100)
    if intraday:
        hrs = pd.Series(bt["peak_hours"]).value_counts()
        print("종목별 거래량 최대 시간대:", ", ".join(f"{h}시 {n}종목" for h, n in hrs.items()))
    with pd.option_context("display.width", 200, "display.max_columns", 30):
        print(summ.to_string(index=False))
    best = summ.dropna(subset=["초과%p"]).sort_values(f"{cfg.days}일돌파%(판정)", ascending=False)
    if not best.empty:
        b = best.iloc[0]
        ok = b[f"{cfg.days}일돌파%(판정)"] >= 60
        print(f"\n최고 전략: {b['전략']}  {cfg.days}일 내 돌파율 {b[f'{cfg.days}일돌파%(판정)']}% "
              f"(무작위 {b['무작위기준%']}%) → 60% 목표 {'달성' if ok else '미달'}")
        r = bt["results"][b["전략"]]
        r.assign(name=r["ticker"].map(lambda t: names.get(t, t))).to_csv(a.log, index=False, encoding="utf-8-sig")
        print(f"일별 픽 로그: {a.log}")
        print("\n최근 5거래일 픽:")
        last = r[r["date"].isin(sorted(r["date"].unique())[-5:])]
        for dt, g in last.groupby("date"):
            s = "  ".join(f"{names.get(t, t)}({'O' if h == 1 else 'X'})" for t, h in zip(g["ticker"], g["hit"]))
            print(f"  {dt.date()}  {s}")

    strat = best.iloc[0]["전략"] if not best.empty else "combo"
    tp = today_picks(frames, cfg, strat, names)
    print(f"\n[다음 거래일 관찰 종목 · {strat} 전략]")
    print(tp.to_string(index=False) if not tp.empty else "  조건 충족 종목 없음")
    if not tp.empty and not tp["market_ok"].iloc[0] and cfg.regime_filter:
        print("  ※ 시장 추세 필터 OFF 구간: 백테스트 규칙상 오늘은 매매하지 않는 날입니다.")
    if a.journal and not tp.empty:
        j = update_journal(a.journal, tp, frames, cfg)
        done = j[j["result"].isin(["HIT", "MISS"])]
        rate = f"{(done['result'] == 'HIT').mean() * 100:.1f}%" if len(done) else "-"
        print(f"\n저널 {a.journal}: 누적 {len(j)}건, 채점 {len(done)}건, 실전 돌파율 {rate}")
    print("\n※ 과거 데이터 백테스트 결과이며 수익을 보장하지 않습니다. 슬리피지·수수료·세금 미반영.")


if __name__ == "__main__":
    main()
