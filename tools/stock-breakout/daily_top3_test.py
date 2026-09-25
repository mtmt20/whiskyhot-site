#!/usr/bin/env python3
"""
날마다 3종목 전고점 돌파 테스트 (Daily Top-3 Breakout Walk-Forward Test)

실존 매매기법과 30여 개 지표로 매일 상위 3종목을 고르고, 2~3거래일 안에
전고점을 돌파했는지 과거 전 구간에서 하루씩 검증한다.
t일 종가까지의 정보로만 고르고 t+1일에 진입하므로 미래 데이터를 쓰지 않는다.

전략
  minervini  트렌드 템플릿 + VCP·스퀴즈
  oneil      RS 등급·RS선 신고가 + 거래량 매집
  wyckoff    OBV·CMF·MFI·포켓피벗 등 매집 + 변동성 수축
  darvas     좁은 박스 상단
  squeeze    볼린저·켈트너 스퀴즈, NR7, ATR 수축
  supply     매물대(위쪽 대기물량)·앵커드 VWAP·윗꼬리
  theme      주도 테마·신기술 모멘텀
  flows      외국인·기관 순매수, 공매도 잔고 감소 (수급 데이터 있을 때)
  combo      위 요소 혼합
  ml         모든 지표로 워크포워드 로지스틱 회귀, 확률 ≥ --min-prob 일 때만 매수

매매시간 고정 (거래량이 몰리는 시간)
  일봉 모드: t+1 시가(09:00 동시호가) 진입 → 목표/손절/N일째 종가(15:20 동시호가) 청산
  분봉 모드(--intraday): 종목별 거래량 최대 60분봉 시각에 진입·청산

사용 예
  python daily_top3_test.py                         # 한국 유니버스 5년, 전 전략
  python daily_top3_test.py --flows                 # KRX 수급 포함 (KRX_ID/KRX_PW 필요)
  python daily_top3_test.py --strategy ml --min-prob 0.65 --days 2
  python daily_top3_test.py --intraday --days 2
  python daily_top3_test.py --journal picks.csv     # 오늘 3종목 기록 + 지난 픽 채점
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from breakout_finder import KR_UNIVERSE, US_UNIVERSE, load_from_csv_dir, load_from_yfinance  # noqa: E402
from indicators import FEATURES, TestConfig, compute  # noqa: E402
from flows import fetch_flows, load_flows  # noqa: E402

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

# 지표 그룹: (지표, 높을수록 좋은가)
SIGN = dict(FEATURES)
GROUPS: Dict[str, List[str]] = {
    "trend": ["trend", "hi52", "dmi_bull", "ichimoku", "adx"],
    "rs": ["rs_raw", "rsline_high", "ret20"],
    "vcp": ["contraction", "range10", "vol_dryup", "squeeze_days", "bbw_pct", "nr7", "atr_contract"],
    "accum": ["ud_ratio", "obv_slope", "obv_div", "obv_high", "cmf", "mfi", "force",
              "pocket_pivots", "vol_spike_up", "clv5"],
    "supply": ["overhead_supply", "avwap_gap", "upper_wick5"],
    "flows": ["flow20", "foreign_streak", "short_chg20"],
    "box": ["box"],
    "prox": ["dist"],
}
STRATEGIES: Dict[str, Dict[str, float]] = {
    "minervini": {"trend": 0.40, "vcp": 0.35, "rs": 0.15, "prox": 0.10},
    "oneil":     {"rs": 0.40, "accum": 0.30, "trend": 0.15, "prox": 0.15},
    "wyckoff":   {"accum": 0.55, "vcp": 0.25, "prox": 0.20},
    "darvas":    {"box": 0.60, "rs": 0.20, "prox": 0.20},
    "squeeze":   {"vcp": 0.60, "trend": 0.20, "prox": 0.20},
    "supply":    {"supply": 0.60, "accum": 0.20, "prox": 0.20},
    "theme":     {"theme": 0.50, "rs": 0.30, "prox": 0.20},
    "flows":     {"flows": 0.60, "accum": 0.20, "prox": 0.20},
    "combo":     {"trend": 0.12, "vcp": 0.12, "rs": 0.12, "accum": 0.14, "supply": 0.12,
                  "flows": 0.10, "box": 0.04, "theme": 0.12, "prox": 0.12},
}

FEATURE_KO = {
    "dist": "전고점 이격", "days_since_high": "전고점 경과일", "touches60": "전고점 근접 횟수",
    "trend": "미너비니 트렌드템플릿", "hi52": "52주 신고가 근접도", "disparity20": "20일 이격도",
    "adx": "ADX 추세강도", "dmi_bull": "DMI 매수우위", "ichimoku": "일목균형표 호조",
    "rs_raw": "RS 상대강도", "ret20": "20일 수익률", "rsline_high": "RS선 신고가 근접",
    "contraction": "변동성 수축비", "range10": "10일 레인지 폭",
    "vol_dryup": "거래량 고갈", "squeeze_days": "TTM 스퀴즈 지속", "bbw_pct": "볼린저밴드폭 백분위",
    "nr7": "NR7(7일 최소폭)", "atr_contract": "ATR 수축", "ud_ratio": "상승/하락 거래량비",
    "obv_slope": "OBV 기울기", "obv_div": "OBV 매집 다이버전스", "obv_high": "OBV 선행 신고가",
    "cmf": "CMF 자금흐름", "mfi": "MFI 자금흐름지수", "force": "엘더 포스인덱스",
    "pocket_pivots": "포켓피벗 횟수", "vol_spike_up": "대량거래 양봉 횟수", "clv5": "종가 위치(CLV)",
    "upper_wick5": "윗꼬리 비율", "overhead_supply": "위쪽 매물대 비중", "avwap_gap": "고점앵커 VWAP 대비",
    "box": "다바스 박스 상단", "rsi14": "RSI", "flow20": "외국인+기관 순매수 강도",
    "foreign_streak": "외국인 연속 순매수", "short_chg20": "공매도잔고 감소",
    "theme": "테마 강도", "breadth": "시장 폭(50일선 위 비율)",
}


# --------------------------------------------------------------------------- #
# 패널 / 점수
# --------------------------------------------------------------------------- #
def panel(frames: Dict[str, pd.DataFrame], col: str) -> pd.DataFrame:
    return pd.DataFrame({t: f[col] for t, f in frames.items()})


def market_index(data: Dict[str, pd.DataFrame]) -> pd.Series:
    """유니버스 동일가중 지수 (일간수익률 평균 누적)."""
    rets = pd.DataFrame({t: df["Close"].pct_change() for t, df in data.items()})
    return (1 + rets.mean(axis=1).fillna(0)).cumprod()


def build_context(frames: Dict[str, pd.DataFrame], data: Dict[str, pd.DataFrame],
                  cfg: TestConfig) -> dict:
    names = [f for f, _ in FEATURES]
    P = {k: panel(frames, k) for k in names + ["liquidity", "Close", "hit", "hit1", "hit2", "hit3",
                                               "pnl", "gap_entry", "stopped", "prior_high"]}
    # 날짜별 단면 백분위 (방향 보정: 높을수록 좋은 쪽)
    R = {k: P[k].rank(axis=1, pct=True, ascending=SIGN[k]) for k in names}
    avail = {k for k in names if P[k].notna().any().any()}

    comp: Dict[str, pd.DataFrame] = {}
    for g, cols in GROUPS.items():
        cs = [R[k] for k in cols if k in avail]
        comp[g] = (sum(x.fillna(0.5) for x in cs) / len(cs)) if cs else None

    ret20 = P["ret20"]
    strength = {th: ret20[[t for t in ts if t in ret20]].mean(axis=1)
                for th, ts in THEMES.items() if any(t in ret20 for t in ts)}
    theme = pd.DataFrame(0.0, index=ret20.index, columns=ret20.columns)
    if strength:
        ts_rank = pd.DataFrame(strength).rank(axis=1, pct=True)
        for t in ret20.columns:
            ths = [th for th in TICKER_THEMES.get(t, []) if th in ts_rank]
            if ths:
                theme[t] = ts_rank[ths].max(axis=1)
    comp["theme"] = theme

    idx = market_index(data).reindex(ret20.index)
    ma50 = P["Close"].rolling(50).mean()
    breadth = (P["Close"] > ma50).sum(axis=1) / P["Close"].notna().sum(axis=1).replace(0, np.nan)
    regime_ok = (idx > idx.rolling(20).mean()) & (breadth > 0.4)

    liq_ok = P["liquidity"].rank(axis=1, pct=True) >= 0.2
    eligible = (P["dist"] >= 0) & (P["dist"] <= cfg.near) & liq_ok

    return {"P": P, "R": R, "comp": comp, "avail": avail, "eligible": eligible,
            "regime_ok": regime_ok, "breadth": breadth, "theme": theme}


def strategy_score(comp, weights) -> Optional[pd.DataFrame]:
    parts = [(comp[k], w) for k, w in weights.items() if comp.get(k) is not None]
    if not parts:
        return None
    tot = sum(w for _, w in parts)
    return sum(x.fillna(0) * (w / tot) for x, w in parts)


# --------------------------------------------------------------------------- #
# 워크포워드 ML (L2 로지스틱 회귀, 뉴턴법)
# --------------------------------------------------------------------------- #
def fit_logit(X: np.ndarray, y: np.ndarray, l2: float = 5.0, iters: int = 25) -> np.ndarray:
    Xb = np.c_[np.ones(len(X)), X]
    w = np.zeros(Xb.shape[1])
    reg = np.eye(Xb.shape[1]) * l2
    reg[0, 0] = 0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(Xb @ w, -30, 30)))
        g = Xb.T @ (p - y) + reg @ w
        H = (Xb * (p * (1 - p))[:, None]).T @ Xb + reg
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-6:
            break
    return w


def predict_logit(w: np.ndarray, X: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-np.clip(np.c_[np.ones(len(X)), X] @ w, -30, 30)))


def event_table(ctx: dict) -> pd.DataFrame:
    """후보(eligible) 행만 모아 ML·지표 리포트용 테이블 생성."""
    elig = ctx["eligible"]
    cols = sorted(ctx["avail"])
    stack = {k: ctx["R"][k].where(elig).stack() for k in cols}
    ev = pd.DataFrame(stack)
    ev["theme"] = ctx["theme"].where(elig).stack()
    b = ctx["breadth"]
    ev["breadth"] = b.reindex(ev.index.get_level_values(0)).values
    for k in ("hit", "hit1", "hit2", "hit3", "pnl", "gap_entry", "stopped"):
        ev[k] = ctx["P"][k].stack().reindex(ev.index)
    raw = {k: ctx["P"][k].stack().reindex(ev.index) for k in cols}
    ev = ev.join(pd.DataFrame(raw).add_prefix("raw_"))
    ev.index.names = ["date", "ticker"]
    return ev


def ml_feature_cols(ev: pd.DataFrame) -> List[str]:
    return [c for c in ev.columns if not c.startswith("raw_")
            and c not in ("hit", "hit1", "hit2", "hit3", "pnl", "gap_entry", "stopped")]


def walk_forward_probs(ev: pd.DataFrame, dates: pd.Index, cfg: TestConfig,
                       retrain_every: int = 20, min_train_days: int = 250) -> pd.Series:
    feats = ml_feature_cols(ev)
    X_all = ev[feats].fillna(0.5).values
    date_pos = pd.Series(np.arange(len(dates)), index=dates)
    ev_pos = date_pos.reindex(ev.index.get_level_values(0)).values
    probs = pd.Series(np.nan, index=ev.index)
    y_all = ev["hit"].values
    first = int(np.nanmin(ev_pos)) + min_train_days if len(ev_pos) else len(dates)
    for start in range(first, len(dates), retrain_every):
        known = (ev_pos <= start - cfg.days - 1) & ~np.isnan(y_all)  # 라벨이 확정된 과거만
        if known.sum() < 300 or len(np.unique(y_all[known])) < 2:
            continue
        w = fit_logit(X_all[known], y_all[known])
        seg = (ev_pos >= start) & (ev_pos < start + retrain_every)
        if seg.any():
            probs.values[seg] = predict_logit(w, X_all[seg])
    return probs


# --------------------------------------------------------------------------- #
# 분봉: 거래량 최대 시간대 고정 매매
# --------------------------------------------------------------------------- #
def peak_volume_hour(intra: pd.DataFrame) -> int:
    return int(intra.groupby(intra.index.hour)["Volume"].mean().idxmax())


def intraday_outcome(intra, window_days, target, hour, cost) -> Optional[dict]:
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
    before = day1[day1.index < ebar.name]
    hit = bool((not before.empty and before["High"].max() > target) or entry >= target
               or (not after.empty and after["High"].max() > target))
    if entry >= target:
        return {"hit": float(hit), "pnl": np.nan, "gap": 1.0}
    lastday = intra[intra.index.normalize() == window_days[-1]]
    xbar = lastday[lastday.index.hour == hour]
    exit_px = float(((xbar["High"] + xbar["Low"] + xbar["Close"]) / 3).iloc[0]) if not xbar.empty \
        else float(lastday["Close"].iloc[-1])
    tgt_hit_after = not after.empty and after["High"].max() > target
    pnl = (target / entry - 1) if tgt_hit_after else (exit_px / entry - 1)
    return {"hit": float(hit), "pnl": pnl - cost, "gap": 0.0}


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
def backtest(ctx: dict, ev: pd.DataFrame, cfg: TestConfig, strategies: List[str],
             intraday: Optional[Dict[str, pd.DataFrame]] = None) -> Dict[str, pd.DataFrame]:
    elig, regime, P = ctx["eligible"], ctx["regime_ok"], ctx["P"]
    all_dates = elig.index
    dates = all_dates[cfg.start_offset:]
    peak_hours = {t: peak_volume_hour(df) for t, df in (intraday or {}).items()}
    ctx["peak_hours"] = peak_hours
    ml_probs = walk_forward_probs(ev, all_dates, cfg) if "ml" in strategies else None
    ctx["ml_probs"] = ml_probs

    results = {}
    for name in strategies:
        if name == "ml":
            score = ml_probs.unstack() if ml_probs is not None else None
        else:
            score = strategy_score(ctx["comp"], STRATEGIES[name])
        if score is None:
            continue
        score = score.reindex(index=all_dates, columns=elig.columns)
        rows = []
        for dt in dates:
            if cfg.regime_filter and not bool(regime.get(dt, False)):
                continue
            e = elig.loc[dt] & P["hit"].loc[dt].notna()
            if intraday is not None:
                e &= e.index.to_series().isin(intraday.keys())
            pool = e[e].index
            if len(pool) == 0:
                continue
            s = score.loc[dt, pool].dropna()
            if name == "ml":
                s = s[s >= cfg.min_prob]
            top = s.sort_values(ascending=False).head(cfg.picks)
            pool_rate = float(P["hit"].loc[dt, pool].mean())
            for t, sc in top.items():
                row = {"date": dt, "ticker": t, "score": float(sc), "pool_rate": pool_rate,
                       "pool_n": len(pool), "hit1": P["hit1"].loc[dt, t], "hit2": P["hit2"].loc[dt, t],
                       "hit3": P["hit3"].loc[dt, t], "hit": P["hit"].loc[dt, t],
                       "pnl": P["pnl"].loc[dt, t], "gap": P["gap_entry"].loc[dt, t],
                       "stopped": P["stopped"].loc[dt, t]}
                if intraday is not None:
                    pos = all_dates.get_loc(dt)
                    window = list(all_dates[pos + 1: pos + 1 + cfg.days])
                    if len(window) < cfg.days:
                        continue
                    tgt = float(P["prior_high"].loc[dt, t]) * (1 + cfg.margin)
                    io = intraday_outcome(intraday[t], window, tgt, peak_hours[t], cfg.cost)
                    if io is None:
                        continue
                    row.update(hit=io["hit"], pnl=io["pnl"], gap=io["gap"], stopped=np.nan)
                rows.append(row)
        results[name] = pd.DataFrame(rows)
    return results


def wilson(k: float, n: int, z: float = 1.96):
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (c - h, c + h)


def summarize(results: Dict[str, pd.DataFrame], cfg: TestConfig) -> pd.DataFrame:
    rows = []
    for name, r in results.items():
        if r.empty:
            rows.append({"전략": name, "픽수": 0})
            continue
        n = len(r)
        k = r["hit"].sum()
        p, p0 = k / n, r["pool_rate"].mean()
        lo, hi = wilson(k, n)
        z = (p - p0) / math.sqrt(max(p0 * (1 - p0), 1e-9) / n)
        pval = 0.5 * math.erfc(z / math.sqrt(2))
        t = r["pnl"].dropna()
        gains, losses = t[t > 0].sum(), -t[t < 0].sum()
        rows.append({
            "전략": name, "매매일": r["date"].nunique(), "픽수": n,
            "2일%": round(r["hit2"].mean() * 100, 1), "3일%": round(r["hit3"].mean() * 100, 1),
            "판정%": round(p * 100, 1), "95%구간": f"{lo*100:.0f}~{hi*100:.0f}",
            "기준%": round(p0 * 100, 1), "초과%p": round((p - p0) * 100, 1),
            "p값": f"{pval:.3f}" if pval >= 0.001 else "<0.001",
            "≥2/3일%": round((r.groupby("date")["hit"].sum() >= 2).mean() * 100, 1),
            "평균손익%": round(t.mean() * 100, 2) if len(t) else np.nan,
            "승률%": round((t > 0).mean() * 100, 1) if len(t) else np.nan,
            "손익비PF": round(gains / losses, 2) if losses > 0 else np.nan,
            "손절%": round(r["stopped"].mean() * 100, 1) if r["stopped"].notna().any() else np.nan,
            "갭%": round(r["gap"].mean() * 100, 1),
        })
    return pd.DataFrame(rows)


def indicator_report(ev: pd.DataFrame) -> pd.DataFrame:
    """지표별 돌파 예측력: 순위상관(IC)과 상위/하위 20% 돌파율 차이 (전 기간, 설명용)."""
    lab = ev.dropna(subset=["hit"])
    base = lab["hit"].mean()
    rows = []
    for c in ml_feature_cols(ev):
        x = lab[c]
        m = x.notna()
        if m.sum() < 200 or x[m].nunique() < 3:
            continue
        ic = x[m].rank().corr(lab.loc[m, "hit"])
        q = x[m].rank(pct=True)
        top, bot = lab.loc[m, "hit"][q > 0.8].mean(), lab.loc[m, "hit"][q <= 0.2].mean()
        rows.append({"지표": FEATURE_KO.get(c, c), "코드": c, "IC": round(ic, 3),
                     "상위20%돌파%": round(top * 100, 1), "하위20%돌파%": round(bot * 100, 1),
                     "차이%p": round((top - bot) * 100, 1)})
    out = pd.DataFrame(rows)
    out.attrs["base"] = base
    return out.sort_values("IC", key=lambda s: -s.abs()) if not out.empty else out


# --------------------------------------------------------------------------- #
# 오늘 픽 / 저널
# --------------------------------------------------------------------------- #
def today_picks(ctx, ev, frames, cfg, strategy, names) -> pd.DataFrame:
    elig = ctx["eligible"]
    dt = elig.index[-1]
    pool = elig.loc[dt][elig.loc[dt]].index
    if len(pool) == 0:
        return pd.DataFrame()
    feats = ml_feature_cols(ev)
    lab = ev.dropna(subset=["hit"])
    prob = pd.Series(np.nan, index=pool)
    if len(lab) >= 300:
        w = fit_logit(lab[feats].fillna(0.5).values, lab["hit"].values)
        cur = ev.xs(dt, level="date").reindex(pool)
        prob = pd.Series(predict_logit(w, cur[feats].fillna(0.5).values), index=pool)
    if strategy == "ml":
        top = prob[prob >= cfg.min_prob].sort_values(ascending=False).head(cfg.picks)
    else:
        score = strategy_score(ctx["comp"], STRATEGIES[strategy])
        top = score.loc[dt, pool].sort_values(ascending=False).head(cfg.picks)
    out = []
    for t, sc in top.items():
        f = frames[t].loc[dt]
        out.append({"date": dt.date(), "ticker": t, "name": names.get(t, t), "strategy": strategy,
                    "score": round(float(sc), 3), "ml_prob": round(float(prob.get(t, np.nan)), 3),
                    "close": round(f["Close"], 2), "prior_high": round(f["prior_high"], 2),
                    "dist%": round(f["dist"] * 100, 2),
                    "stop": round(f["Close"] - cfg.stop_atr * f["atr14"], 2) if cfg.stop_atr > 0 else np.nan,
                    "themes": "/".join(TICKER_THEMES.get(t, [])),
                    "supply%": round(f["overhead_supply"] * 100, 1) if pd.notna(f["overhead_supply"]) else np.nan,
                    "squeeze": round(f["squeeze_days"], 1), "cmf": round(f["cmf"], 3),
                    "market_ok": bool(ctx["regime_ok"].loc[dt])})
    return pd.DataFrame(out)


def update_journal(path: str, picks: pd.DataFrame, frames, cfg: TestConfig) -> pd.DataFrame:
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
        if r["result"] in ("HIT", "MISS"):
            continue
        f = frames.get(r["ticker"])
        if f is None:
            continue
        after = f[f.index > r["date"]].head(cfg.days)
        if after.empty:
            j.at[i, "result"] = "PENDING"
            continue
        j.at[i, "entry_open"] = round(float(after["Open"].iloc[0]), 2)
        j.at[i, "max_high"] = round(float(after["High"].max()), 2)
        if after["High"].max() > r["prior_high"] * (1 + cfg.margin):
            j.at[i, "result"] = "HIT"
        else:
            j.at[i, "result"] = "MISS" if len(after) >= cfg.days else "PENDING"
    j.to_csv(path, index=False, encoding="utf-8-sig")
    return j


# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description="날마다 3종목 2~3일 전고점 돌파 테스트")
    ap.add_argument("--market", choices=["kr", "us"], default="kr")
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--csv-dir", help="오프라인 일봉 CSV 디렉터리")
    ap.add_argument("--period", default="5y")
    ap.add_argument("--strategy", choices=list(STRATEGIES) + ["ml", "all"], default="all")
    ap.add_argument("--days", type=int, default=3, help="돌파 판정 거래일 (2 또는 3)")
    ap.add_argument("--picks", type=int, default=3)
    ap.add_argument("--near", type=float, default=0.05)
    ap.add_argument("--margin", type=float, default=0.0)
    ap.add_argument("--cost", type=float, default=0.0025, help="왕복 비용 (기본 0.25%%)")
    ap.add_argument("--stop-atr", type=float, default=1.5, help="ATR 손절 배수, 0=손절 없음")
    ap.add_argument("--take-profit", type=float, default=0.0,
                    help="익절: 전고점 대비 +비율에서 매도 (예 0.03). 0=전고점 도달 즉시")
    ap.add_argument("--min-prob", type=float, default=0.60, help="ml 전략 최소 확률")
    ap.add_argument("--no-regime", action="store_true", help="시장 추세 필터 끄기")
    ap.add_argument("--flows", action="store_true", help="KRX 외국인·기관 수급/공매도 받아서 사용")
    ap.add_argument("--flows-dir", default=os.path.join(HERE, "flows_cache"), help="수급 CSV 캐시 폴더")
    ap.add_argument("--intraday", action="store_true", help="60분봉 거래량 최대 시간대로 매매시각 고정")
    ap.add_argument("--intraday-csv-dir", help="오프라인 60분봉 CSV (Datetime,Open,High,Low,Close,Volume)")
    ap.add_argument("--journal", help="오늘 픽 기록/과거 픽 채점할 CSV 경로")
    ap.add_argument("--log", default="daily_top3_log.csv", help="최고 전략의 일별 픽 로그")
    a = ap.parse_args()

    cfg = TestConfig(near=a.near, days=a.days, picks=a.picks, margin=a.margin, cost=a.cost,
                     stop_atr=a.stop_atr, min_prob=a.min_prob, take_profit=a.take_profit, regime_filter=not a.no_regime)
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

    if a.flows and not a.csv_dir:
        first = min(df.index[0] for df in data.values()).strftime("%Y%m%d")
        last = max(df.index[-1] for df in data.values()).strftime("%Y%m%d")
        print("KRX 수급 데이터 다운로드 중...", file=sys.stderr)
        fetch_flows(list(data), first, last, a.flows_dir)
    flows = load_flows(a.flows_dir) if (a.flows or os.path.isdir(a.flows_dir)) else {}

    mkt = market_index(data)
    print("지표 계산 중...", file=sys.stderr)
    frames = {t: compute(df, cfg, market=mkt, flows=flows.get(t)) for t, df in data.items()}
    ctx = build_context(frames, data, cfg)
    ev = event_table(ctx)

    intraday = None
    if a.intraday or a.intraday_csv_dir:
        intraday = load_intraday(list(frames), a.intraday_csv_dir)
        if not intraday:
            sys.exit("분봉 데이터를 불러오지 못했습니다.")

    if a.strategy == "all":
        strategies = [s for s in STRATEGIES if s != "flows" or "flow20" in ctx["avail"]] + ["ml"]
    else:
        strategies = [a.strategy]
    results = backtest(ctx, ev, cfg, strategies, intraday)
    summ = summarize(results, cfg)

    mode = "60분봉·거래량 최대 시간대" if intraday else "일봉·시가 진입/종가 청산"
    print("=" * 110)
    print(f" 날마다 {cfg.picks}종목 · {cfg.days}거래일 내 전고점 돌파 | {mode} | 비용 {cfg.cost*100:.2f}% | "
          f"손절 ATR×{cfg.stop_atr} | 익절 +{cfg.take_profit*100:.0f}% | 시장필터 {'ON' if cfg.regime_filter else 'OFF'} | 종목 {len(frames)} | "
          f"수급 {'있음' if 'flow20' in ctx['avail'] else '없음'}")
    print("=" * 110)
    if intraday:
        hrs = pd.Series(ctx["peak_hours"]).value_counts()
        print("종목별 거래량 최대 시간대:", ", ".join(f"{h}시 {n}종목" for h, n in hrs.items()))
    with pd.option_context("display.width", 250, "display.max_columns", 30):
        print(summ.to_string(index=False))

    rep = indicator_report(ev)
    if not rep.empty:
        print(f"\n[지표별 돌파 예측력]  후보 전체 {cfg.days}일 돌파율 {rep.attrs['base']*100:.1f}%  "
              f"(IC: 순위상관, +면 높을수록 돌파 잘 됨)")
        with pd.option_context("display.width", 200):
            print(rep.head(15).drop(columns=["코드"]).to_string(index=False))
        rep.to_csv("indicator_report.csv", index=False, encoding="utf-8-sig")
        print("  전체 표: indicator_report.csv")

    probs = ctx.get("ml_probs")
    if probs is not None and probs.notna().any():
        m = probs.notna() & ev["hit"].notna()
        pr, y = probs[m], ev.loc[m, "hit"]
        bins = pd.cut(pr, [0, .4, .5, .6, .7, .8, 1.0])
        cal = y.groupby(bins, observed=True).agg(["mean", "count"])
        print("\n[ML 확률 캘리브레이션 · 표본 외] " + ", ".join(
            f"{iv}: 실제 {r['mean']*100:.0f}% (n={int(r['count'])})" for iv, r in cal.iterrows()))

    ok = summ.dropna(subset=["초과%p"])
    ok = ok[ok["픽수"] >= 30]
    best = ok.sort_values("판정%", ascending=False).iloc[0] if not ok.empty else None
    strat = best["전략"] if best is not None else "combo"
    if best is not None:
        print(f"\n최고 전략: {strat}  {cfg.days}일 내 돌파율 {best['판정%']}% (95% {best['95%구간']}, "
              f"기준 {best['기준%']}%) → 60% 목표 {'달성' if best['판정%'] >= 60 else '미달'}")
        r = results[strat]
        r.assign(name=r["ticker"].map(lambda t: names.get(t, t))).to_csv(a.log, index=False, encoding="utf-8-sig")
        print(f"일별 픽 로그: {a.log}")
        print("최근 5거래일 픽:")
        for dt, g in r[r["date"].isin(sorted(r["date"].unique())[-5:])].groupby("date"):
            print(f"  {dt.date()}  " + "  ".join(
                f"{names.get(t, t)}({'O' if h == 1 else 'X'})" for t, h in zip(g["ticker"], g["hit"])))

    tp = today_picks(ctx, ev, frames, cfg, strat, names)
    print(f"\n[다음 거래일 관찰 종목 · {strat}]  시가 매수, 전고점+{cfg.take_profit*100:.0f}% 도달 시 매도, 손절가 이탈 시 매도, "
          f"{cfg.days}일째 종가 청산")
    with pd.option_context("display.width", 250, "display.max_columns", 30):
        print(tp.to_string(index=False) if not tp.empty else "  조건 충족 종목 없음")
    if not tp.empty and cfg.regime_filter and not tp["market_ok"].iloc[0]:
        print("  ※ 시장 필터 OFF: 백테스트 규칙상 오늘은 매매하지 않는 날입니다.")
    if a.journal and not tp.empty:
        j = update_journal(a.journal, tp, frames, cfg)
        done = j[j["result"].isin(["HIT", "MISS"])]
        rate = f"{(done['result'] == 'HIT').mean()*100:.1f}%" if len(done) else "-"
        print(f"\n저널 {a.journal}: 누적 {len(j)}건, 채점 {len(done)}건, 실전 돌파율 {rate}")
    print("\n※ 과거 백테스트이며 수익을 보장하지 않습니다. 상장폐지 종목이 빠진 생존편향이 있습니다.")


if __name__ == "__main__":
    main()
