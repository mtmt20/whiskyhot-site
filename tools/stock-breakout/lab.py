#!/usr/bin/env python3
"""
전략 실험실 (Strategy Lab)

여러 투자법을 같은 종목·같은 비용·같은 매매 규칙으로 돌려 확률과 수익성을 비교하고,
가장 좋은 방법을 골라 나간다. 실행할 때마다 결과를 lab_history.csv에 쌓는다.

과최적화 방지
  - 기간을 앞 70%(학습)와 뒤 30%(검증)로 나눈다.
  - 전략마다 여러 설정(변형)을 학습 구간에서만 비교해 하나를 고른다.
  - 순위와 채택 여부는 검증 구간 성적으로만 매긴다.
  - 여러 전략을 동시에 시험한 만큼 p값을 본페로니 방식으로 보정한다.

전략
  breakout_near     전고점 아래 접근 + 추세 (단기 돌파 트랙)
  breakout_pivot    전고점 종가 돌파 + 거래량 1.5배 (O'Neil 매수점)
  squeeze_break     변동성 스퀴즈 후 20일 고점 돌파
  momentum          상대강도 상위 10% + 추세, 월초 진입
  high52            52주 신고가 근접 모멘텀
  rsi2_reversion    200일선 위 단기 과매도 반등 (Connors RSI2)
  accumulation      거래량 매집 지표 상위 + 추세
  ml_breakout       워크포워드 ML 돌파확률 ≥ 기준
  cycle_frontrun    주기적 거래량 급증 선취매
  events            사용자가 준 이벤트 CSV (증여·소각 공시일 등) 이후 보유
  ensemble          학습 구간 상위 3개 전략 중 2개 이상이 동시에 신호

사용 예
  python lab.py                                  # 한국 기본 유니버스 5년
  python lab.py --csv-dir ./ohlcv --max-pos 5
  python lab.py --events my_events.csv           # code,date,type
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import datetime
from typing import Dict, List

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from breakout_finder import KR_UNIVERSE, US_UNIVERSE, load_from_csv_dir, load_from_yfinance  # noqa: E402
from indicators import TestConfig, compute, rsi  # noqa: E402

COST = 0.0025


# --------------------------------------------------------------------------- #
# 데이터 준비
# --------------------------------------------------------------------------- #
def prepare(data: Dict[str, pd.DataFrame], flows: Dict[str, pd.DataFrame]) -> dict:
    from daily_top3_test import market_index
    cfg = TestConfig(near=0.05, days=3)
    mkt = market_index(data)
    frames = {}
    for t, df in data.items():
        d = compute(df, cfg, market=mkt, flows=flows.get(t))
        c = d["Close"]
        d["rsi2"] = rsi(c, 2)
        d["ma5"] = c.rolling(5).mean()
        d["ma200"] = c.rolling(200).mean()
        d["vol_ratio"] = d["Volume"] / d["Volume"].rolling(50).mean().shift()
        d["hi20_prev"] = d["High"].rolling(20).max().shift()
        frames[t] = d
    P = lambda col: pd.DataFrame({t: f[col] for t, f in frames.items()})  # noqa: E731
    idx = mkt.reindex(P("Close").index)
    close = P("Close")
    breadth = (close > close.rolling(50).mean()).sum(axis=1) / close.notna().sum(axis=1).replace(0, np.nan)
    regime = (idx > idx.rolling(20).mean()) & (breadth > 0.4)
    accum_cols = ["ud_ratio", "obv_slope", "cmf", "pocket_pivots", "obv_div"]
    accum = sum(P(c).rank(axis=1, pct=True).fillna(0.5) for c in accum_cols) / len(accum_cols)
    return {"cfg": cfg, "frames": frames, "data": data, "P": P, "regime": regime,
            "rs_rank": P("rs_raw").rank(axis=1, pct=True), "accum_rank": accum.rank(axis=1, pct=True),
            "dates": close.index, "tickers": list(frames)}


# --------------------------------------------------------------------------- #
# 매매 시뮬레이터 (종목별, 겹치지 않게)
# --------------------------------------------------------------------------- #
def simulate(d: pd.DataFrame, mask: np.ndarray, score: np.ndarray, ex: dict, ticker: str) -> List[dict]:
    o, h, l, c = (d[k].values for k in ("Open", "High", "Low", "Close"))
    ph, at = d["prior_high"].values, d["atr14"].values
    exit_sig = d[ex["exit_signal"]].values if ex.get("exit_signal") else None
    n, hold = len(d), ex["hold"]
    trades, busy_until = [], -1
    for t in np.where(mask)[0]:
        if t <= busy_until or t + hold >= n:
            continue
        e = o[t + 1]
        if not np.isfinite(e) or e <= 0:
            continue
        if ex.get("target") == "prior_high":
            tgt = ph[t] * (1 + ex.get("tp", 0))
            if not np.isfinite(tgt) or e >= tgt:        # 갭으로 이미 목표 위면 추격 안 함
                continue
        elif ex.get("target") == "pct":
            tgt = e * (1 + ex["tp"])
        else:
            tgt = np.inf
        stop = -np.inf
        if ex.get("stop_atr") and np.isfinite(at[t]):
            stop = e - ex["stop_atr"] * at[t]
        if ex.get("stop_pct"):
            stop = max(stop, e * (1 - ex["stop_pct"]))
        px, hit, j = None, False, t + hold
        for i in range(t + 1, t + hold + 1):
            if i > t + 1 and o[i] >= tgt:
                px, hit, j = o[i], True, i; break
            if i > t + 1 and o[i] <= stop:
                px, j = o[i], i; break
            if l[i] <= stop:
                px, j = stop, i; break
            if h[i] >= tgt:
                px, hit, j = tgt, True, i; break
            if exit_sig is not None and i > t + 1 and exit_sig[i]:
                px, j = c[i], i; break
        if px is None:
            px = c[t + hold]
        trades.append({"ticker": ticker, "signal_date": d.index[t], "entry_date": d.index[t + 1],
                       "exit_date": d.index[j], "ret": px / e - 1 - COST, "hit": hit,
                       "hold": j - t, "score": float(score[t]) if np.isfinite(score[t]) else 0.0})
        busy_until = j
    return trades


def run_panel_strategy(ctx: dict, mask: pd.DataFrame, score: pd.DataFrame, ex: dict) -> pd.DataFrame:
    rows = []
    for t in ctx["tickers"]:
        d = ctx["frames"][t]
        m = mask[t].reindex(d.index).fillna(False).values.astype(bool)
        s = score[t].reindex(d.index).values.astype(float)
        rows += simulate(d, m, s, ex, t)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# 전략 정의: (변형 목록, 신호 함수)  신호 함수는 (ctx, 변형) → (mask, score, 청산규칙)
# --------------------------------------------------------------------------- #
def s_breakout_near(ctx, v):
    P = ctx["P"]
    dist, trend = P("dist"), P("trend")
    m = (dist >= 0) & (dist <= v["near"]) & (trend >= v["trend"])
    if v.get("regime", True):
        m = m & ctx["regime"].values[:, None]
    return m, -dist, {"hold": v["hold"], "target": "prior_high", "tp": v["tp"], "stop_atr": v["stop_atr"]}


def s_breakout_pivot(ctx, v):
    P = ctx["P"]
    c, ph = P("Close"), P("prior_high")
    m = (c > ph) & (c.shift() <= ph.shift()) & (P("vol_ratio") >= v["vol"]) & (P("trend") >= 5 / 7)
    m = m & ctx["regime"].values[:, None]
    return m, P("vol_ratio"), {"hold": v["hold"], "target": "pct", "tp": v["tp"], "stop_pct": v["stop_pct"]}


def s_squeeze(ctx, v):
    P = ctx["P"]
    m = (P("squeeze_days").shift() >= v["sq"]) & (P("Close") > P("hi20_prev")) & (P("trend") >= 4 / 7)
    m = m & ctx["regime"].values[:, None]
    return m, P("squeeze_days"), {"hold": v["hold"], "target": "pct", "tp": v["tp"], "stop_atr": 2.0}


def s_momentum(ctx, v):
    P = ctx["P"]
    dates = ctx["dates"]
    first = pd.Series(dates.to_period("M"), index=dates)
    month_start = (first != first.shift()).values
    m = (ctx["rs_rank"] >= v["rank"]) & (P("trend") >= 6 / 7) & month_start[:, None]
    m = m & ctx["regime"].values[:, None]
    return m, ctx["rs_rank"], {"hold": v["hold"], "stop_atr": v["stop_atr"]}


def s_high52(ctx, v):
    P = ctx["P"]
    m = (P("hi52") >= v["hi"]) & (P("trend") >= 5 / 7) & ctx["regime"].values[:, None]
    return m, P("hi52"), {"hold": v["hold"], "stop_atr": 2.5}


def s_rsi2(ctx, v):
    P = ctx["P"]
    c = P("Close")
    m = (c > P("ma200")) & (P("rsi2") < v["rsi"])
    for t, d in ctx["frames"].items():
        d["_exit_ma5"] = d["Close"] > d["ma5"]
    return m, -P("rsi2"), {"hold": v["hold"], "exit_signal": "_exit_ma5",
                          "stop_atr": v.get("stop_atr")}


def s_accum(ctx, v):
    P = ctx["P"]
    m = (ctx["accum_rank"] >= v["rank"]) & (P("trend") >= 4 / 7) & (P("dist") <= 0.15)
    m = m & ctx["regime"].values[:, None]
    return m, ctx["accum_rank"], {"hold": v["hold"], "stop_atr": 2.5}


def s_ml(ctx, v):
    if "ml_probs" not in ctx:
        from daily_top3_test import build_context, event_table, walk_forward_probs
        c2 = build_context(ctx["frames"], ctx["data"], ctx["cfg"])
        ev = event_table(c2)
        ctx["ml_probs"] = walk_forward_probs(ev, c2["eligible"].index, ctx["cfg"]).unstack()
    pr = ctx["ml_probs"].reindex(index=ctx["dates"], columns=ctx["tickers"])
    m = (pr >= v["p"]) & ctx["regime"].values[:, None]
    return m, pr, {"hold": 3, "target": "prior_high", "tp": v["tp"], "stop_atr": 2.0}


STRATEGIES: Dict[str, tuple] = {
    "breakout_near": ("전고점 접근 + 추세", s_breakout_near, [
        {"near": 0.03, "trend": 5 / 7, "tp": 0.03, "stop_atr": 2.0, "hold": 3},
        {"near": 0.03, "trend": 5 / 7, "tp": 0.0, "stop_atr": 1.5, "hold": 3},
        {"near": 0.05, "trend": 4 / 7, "tp": 0.05, "stop_atr": 2.0, "hold": 5},
        {"near": 0.02, "trend": 6 / 7, "tp": 0.03, "stop_atr": 2.0, "hold": 3}]),
    "breakout_pivot": ("전고점 종가 돌파 + 거래량", s_breakout_pivot, [
        {"vol": 1.5, "tp": 0.10, "stop_pct": 0.07, "hold": 20},
        {"vol": 1.5, "tp": 0.20, "stop_pct": 0.08, "hold": 40},
        {"vol": 2.0, "tp": 0.10, "stop_pct": 0.05, "hold": 10}]),
    "squeeze_break": ("스퀴즈 후 20일 고점 돌파", s_squeeze, [
        {"sq": 0.5, "tp": 0.08, "hold": 10}, {"sq": 0.3, "tp": 0.12, "hold": 20}]),
    "momentum": ("상대강도 상위 + 추세, 월초", s_momentum, [
        {"rank": 0.9, "hold": 20, "stop_atr": 3.0}, {"rank": 0.8, "hold": 60, "stop_atr": 4.0}]),
    "high52": ("52주 신고가 근접", s_high52, [{"hi": 0.97, "hold": 20}, {"hi": 0.99, "hold": 10}]),
    "rsi2_reversion": ("200일선 위 단기 과매도", s_rsi2, [
        {"rsi": 10, "hold": 5}, {"rsi": 5, "hold": 5}, {"rsi": 10, "hold": 5, "stop_atr": 3.0}]),
    "accumulation": ("매집 지표 상위 + 추세", s_accum, [{"rank": 0.9, "hold": 20}, {"rank": 0.95, "hold": 40}]),
    "ml_breakout": ("워크포워드 ML 돌파확률", s_ml, [
        {"p": 0.55, "tp": 0.03}, {"p": 0.60, "tp": 0.03}, {"p": 0.65, "tp": 0.0}]),
}


def run_cycle(ctx: dict, v: dict) -> pd.DataFrame:
    from accumulation import compute_series, cycle_backtest
    rows = []
    for t, df in ctx["data"].items():
        cb = cycle_backtest(compute_series(df), pre=v["pre"], cost=COST)
        if cb:
            tr = cb["trades"].rename(columns={"entry_date": "entry_date", "exit_date": "exit_date"})
            tr["ticker"], tr["signal_date"], tr["score"] = t, tr["entry_date"], 0.0
            tr["hold"] = [(ctx["frames"][t].index.get_loc(x) - ctx["frames"][t].index.get_loc(y) + 1)
                          for x, y in zip(tr["exit_date"], tr["entry_date"])]
            rows.append(tr)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def run_events(ctx: dict, events: pd.DataFrame, v: dict) -> pd.DataFrame:
    rows = []
    for _, e in events[events["type"] == v["type"]].iterrows():
        t = next((k for k in ctx["tickers"] if k.split(".")[0] == str(e["code"]).zfill(6) or k == e["code"]), None)
        if t is None:
            continue
        d = ctx["frames"][t]
        pos = d.index.searchsorted(pd.Timestamp(e["date"])) + v["offset"]
        if pos + 1 + v["hold"] >= len(d):
            continue
        m = np.zeros(len(d), bool)
        m[pos] = True
        rows += simulate(d, m, np.zeros(len(d)), {"hold": v["hold"], "stop_atr": v.get("stop_atr")}, t)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# 성과 측정
# --------------------------------------------------------------------------- #
def metrics(tr: pd.DataFrame) -> dict:
    if tr is None or tr.empty:
        return {"n": 0}
    r = tr["ret"].values
    n = len(r)
    sd = r.std(ddof=1) if n > 1 else np.nan
    tstat = r.mean() / sd * math.sqrt(n) if sd and sd > 0 else 0.0
    gains, losses = r[r > 0].sum(), -r[r < 0].sum()
    return {"n": n, "win": (r > 0).mean() * 100, "avg": r.mean() * 100, "med": float(np.median(r)) * 100,
            "pf": gains / losses if losses > 0 else np.nan, "t": tstat,
            "p": 0.5 * math.erfc(tstat / math.sqrt(2)), "hold": tr["hold"].mean(),
            "hit": tr["hit"].mean() * 100 if "hit" in tr else np.nan}


def portfolio(tr: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, max_pos: int) -> dict:
    """최대 max_pos 종목 동시 보유, 점수 높은 신호 우선. 청산 시점 기준 실현 손익으로 자산 곡선 계산."""
    if tr is None or tr.empty:
        return {}
    tr = tr.sort_values(["entry_date", "score"], ascending=[True, False])
    equity, active, curve, taken = 1.0, [], [(start, 1.0)], 0
    for _, x in tr.iterrows():
        done = [a for a in active if a[0] < x["entry_date"]]
        for a in sorted(done, key=lambda z: z[0]):
            equity += a[1] * a[2]
            curve.append((a[0], equity))
        active = [a for a in active if a[0] >= x["entry_date"]]
        if len(active) < max_pos:
            active.append((x["exit_date"], equity / max_pos, x["ret"]))
            taken += 1
    for a in sorted(active, key=lambda z: z[0]):
        equity += a[1] * a[2]
        curve.append((a[0], equity))
    eq = pd.Series([v for _, v in curve], index=[d for d, _ in curve])
    years = max((end - start).days / 365.25, 0.1)
    mdd = (eq / eq.cummax() - 1).min()
    return {"cagr": (equity ** (1 / years) - 1) * 100, "mdd": mdd * 100, "taken": taken}


def split(tr: pd.DataFrame, cut: pd.Timestamp):
    if tr is None or tr.empty:
        return tr, tr
    return tr[tr["signal_date"] < cut], tr[tr["signal_date"] >= cut]


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description="전략 실험실: 여러 투자법 비교")
    ap.add_argument("--market", choices=["kr", "us"], default="kr")
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--csv-dir")
    ap.add_argument("--flows-dir", default=os.path.join(HERE, "flows_cache"))
    ap.add_argument("--period", default="5y")
    ap.add_argument("--events", help="이벤트 CSV (code,date,type)")
    ap.add_argument("--only", nargs="*", help="이 전략만 실행")
    ap.add_argument("--train", type=float, default=0.7, help="학습 구간 비율")
    ap.add_argument("--max-pos", type=int, default=5, help="포트폴리오 동시 보유 종목 수")
    ap.add_argument("--min-trades", type=int, default=30)
    ap.add_argument("--out-dir", default="lab_results")
    ap.add_argument("--note", default="", help="이번 실험 메모 (기록에 남음)")
    a = ap.parse_args()

    names = dict(KR_UNIVERSE if a.market == "kr" else US_UNIVERSE)
    if a.tickers:
        names = {t: names.get(t, t) for t in a.tickers}
    data = load_from_csv_dir(a.csv_dir, a.tickers) if a.csv_dir else load_from_yfinance(list(names), a.period)
    if not data:
        sys.exit("데이터를 불러오지 못했습니다.")
    from flows import load_flows
    flows = load_flows(a.flows_dir)
    print(f"{len(data)}개 종목 지표 계산 중...", file=sys.stderr)
    ctx = prepare(data, flows)
    dates = ctx["dates"][260:]
    cut = dates[int(len(dates) * a.train)]
    start, end = dates[0], dates[-1]
    os.makedirs(a.out_dir, exist_ok=True)

    def panel_runner(fn):
        def run(v):
            m, s, ex = fn(ctx, v)
            return run_panel_strategy(ctx, m, s, ex)
        return run

    runners: Dict[str, tuple] = {}
    for key, (desc, fn, variants) in STRATEGIES.items():
        runners[key] = (desc, variants, panel_runner(fn))
    runners["cycle_frontrun"] = ("주기적 거래량 급증 선취매", [{"pre": 1}, {"pre": 2}, {"pre": 3}],
                                 lambda v: run_cycle(ctx, v))
    events = None
    if a.events:
        events = pd.read_csv(a.events, dtype={"code": str}, parse_dates=["date"])
        for ty in events["type"].unique():
            runners[f"event:{ty}"] = (f"이벤트 '{ty}' 이후 보유", [
                {"type": ty, "offset": 1, "hold": 20}, {"type": ty, "offset": 1, "hold": 60},
                {"type": ty, "offset": 40, "hold": 60}], (lambda v: run_events(ctx, events, v)))
    if a.only:
        runners = {k: v for k, v in runners.items() if k in a.only}

    rows, chosen = [], {}
    for key, (desc, variants, runner) in runners.items():
        print(f"  {key} ({len(variants)}개 설정)...", file=sys.stderr)
        best = None
        for v in variants:
            tr = runner(v)
            is_tr, oos_tr = split(tr, cut)
            mi = metrics(is_tr)
            rank_key = (mi.get("n", 0) >= a.min_trades, mi.get("t", -99))
            if best is None or rank_key > best[0]:
                best = (rank_key, v, tr, mi)
        _, v, tr, mi = best
        is_tr, oos_tr = split(tr, cut)
        mo = metrics(oos_tr)
        po = portfolio(oos_tr, cut, end, a.max_pos)
        chosen[key] = (v, tr)
        rows.append({"전략": key, "설명": desc, "설정": ", ".join(f"{k}={round(x, 3) if isinstance(x, float) else x}" for k, x in v.items()),
                     "학습_거래": mi.get("n", 0), "학습_승률": mi.get("win"), "학습_평균%": mi.get("avg"),
                     "학습_t": mi.get("t"),
                     "검증_거래": mo.get("n", 0), "검증_승률": mo.get("win"), "검증_평균%": mo.get("avg"),
                     "검증_PF": mo.get("pf"), "검증_목표도달%": mo.get("hit"), "검증_보유일": mo.get("hold"),
                     "검증_p": mo.get("p"), "검증_연수익%": po.get("cagr"), "검증_MDD%": po.get("mdd")})

    # 앙상블: 학습 구간 t값 상위 3개 패널 전략 중 2개 이상 동시 신호
    panel_keys = [r["전략"] for r in sorted(rows, key=lambda r: -(r["학습_t"] or -99))
                  if r["전략"] in STRATEGIES and (r["학습_거래"] or 0) >= a.min_trades][:3]
    if len(panel_keys) >= 2 and (not a.only or "ensemble" in a.only):
        masks, scores = [], []
        for k in panel_keys:
            m, s, _ = STRATEGIES[k][1](ctx, chosen[k][0])
            masks.append(m.fillna(False).astype(int).rolling(3, min_periods=1).max())
            scores.append(s.rank(axis=1, pct=True).fillna(0))
        agree = sum(masks) >= 2
        ex = STRATEGIES[panel_keys[0]][1](ctx, chosen[panel_keys[0]][0])[2]
        tr = run_panel_strategy(ctx, agree, sum(scores), ex)
        is_tr, oos_tr = split(tr, cut)
        mi, mo, po = metrics(is_tr), metrics(oos_tr), portfolio(oos_tr, cut, end, a.max_pos)
        rows.append({"전략": "ensemble", "설명": "상위 3개 중 2개 이상 동시 신호", "설정": "+".join(panel_keys),
                     "학습_거래": mi.get("n", 0), "학습_승률": mi.get("win"), "학습_평균%": mi.get("avg"),
                     "학습_t": mi.get("t"), "검증_거래": mo.get("n", 0), "검증_승률": mo.get("win"),
                     "검증_평균%": mo.get("avg"), "검증_PF": mo.get("pf"), "검증_목표도달%": mo.get("hit"),
                     "검증_보유일": mo.get("hold"), "검증_p": mo.get("p"), "검증_연수익%": po.get("cagr"),
                     "검증_MDD%": po.get("mdd")})
        chosen["ensemble"] = ({}, tr)

    lb = pd.DataFrame(rows)
    m_tests = len(lb)
    lb["검증_p보정"] = (lb["검증_p"] * m_tests).clip(upper=1.0)

    def verdict(r):
        if (r["검증_거래"] or 0) < a.min_trades:
            return "표본 부족"
        if r["검증_평균%"] > 0 and r["검증_p보정"] < 0.05 and (r["검증_PF"] or 0) > 1.1:
            return "채택 후보"
        if r["검증_평균%"] > 0 and (r["학습_평균%"] or 0) > 0:
            return "유망, 추가 검증"
        return "기각"
    lb["판정"] = lb.apply(verdict, axis=1)
    order = {"채택 후보": 0, "유망, 추가 검증": 1, "표본 부족": 2, "기각": 3}
    lb = lb.sort_values(["판정", "검증_평균%"], key=lambda s: s.map(order) if s.name == "판정" else -s.fillna(-99))

    show = ["전략", "판정", "검증_거래", "검증_승률", "검증_평균%", "검증_PF", "검증_p보정", "검증_연수익%",
            "검증_MDD%", "학습_거래", "학습_평균%", "설정"]
    print("=" * 120)
    print(f" 전략 실험실 | 종목 {len(data)} | 학습 {start.date()}~{cut.date()} | 검증 {cut.date()}~{end.date()} | "
          f"비용 {COST*100:.2f}% | 동시보유 {a.max_pos} | 시험 전략 {m_tests}개")
    print("=" * 120)
    with pd.option_context("display.width", 250, "display.max_columns", 30, "display.max_colwidth", 60,
                           "display.float_format", "{:,.2f}".format):
        print(lb[show].to_string(index=False))

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    lb.to_csv(os.path.join(a.out_dir, "leaderboard.csv"), index=False, encoding="utf-8-sig")
    hist = os.path.join(a.out_dir, "lab_history.csv")
    lb.assign(실행=stamp, 종목수=len(data), 메모=a.note).to_csv(
        hist, mode="a", header=not os.path.exists(hist), index=False, encoding="utf-8-sig")
    top = lb.iloc[0]["전략"]
    tr = chosen[top][1]
    if tr is not None and not tr.empty:
        split(tr, cut)[1].to_csv(os.path.join(a.out_dir, f"trades_{top.replace(':', '_')}.csv"),
                                 index=False, encoding="utf-8-sig")
    print(f"\n1위: {top} ({lb.iloc[0]['판정']}). 순위표 {a.out_dir}/leaderboard.csv, 누적 기록 {hist}")
    print("※ 검증 구간 성적만으로 순위를 매겼습니다. 과거 성과는 미래를 보장하지 않습니다.")


if __name__ == "__main__":
    main()
