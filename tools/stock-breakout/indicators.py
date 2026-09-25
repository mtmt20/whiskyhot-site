"""
지표 계산 모듈. 종목 하나의 일봉(OHLCV)을 받아 전고점, 30여 개 지표,
2~3일 돌파 라벨, 매매 시뮬레이션 손익을 계산한다.
각 지표 설명은 INDICATORS.md 참고.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view


@dataclass
class TestConfig:
    lookback: int = 120      # 전고점 산정 기간(거래일)
    gap: int = 5             # 전고점 산정에서 제외할 최근 일수
    near: float = 0.05       # 전고점 아래 이 비율 이내만 후보
    days: int = 3            # 돌파 판정 기간(2~3일)
    picks: int = 3           # 하루 선정 종목 수
    margin: float = 0.0      # 돌파 인정 여유 (0.005 = 전고점 +0.5%)
    regime_filter: bool = True
    start_offset: int = 260  # 지표 준비 기간(52주)
    cost: float = 0.0025     # 왕복 비용(수수료+거래세+슬리피지), 0.25%
    stop_atr: float = 1.5    # 손절: 진입가 - ATR×배수 (0이면 손절 없음)
    min_prob: float = 0.60   # ML 전략 최소 확률
    take_profit: float = 0.0 # 익절: 전고점 × (1+값). 0이면 전고점 도달 즉시 매도
    supply_window: int = 250 # 매물대 계산 기간


# --------------------------------------------------------------------------- #
# 기본 지표 함수
# --------------------------------------------------------------------------- #
def rsi(c: pd.Series, n: int = 14) -> pd.Series:
    d = c.diff()
    ru = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    rd = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return (100 - 100 / (1 + ru / rd.replace(0, np.nan))).fillna(50.0)


def true_range(h, l, c):
    pc = c.shift()
    return pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)


def atr(h, l, c, n=14):
    return true_range(h, l, c).ewm(alpha=1 / n, adjust=False).mean()


def adx(h, l, c, n=14):
    up, dn = h.diff(), -l.diff()
    plus = up.where((up > dn) & (up > 0), 0.0)
    minus = dn.where((dn > up) & (dn > 0), 0.0)
    a = atr(h, l, c, n).replace(0, np.nan)
    pdi = 100 * plus.ewm(alpha=1 / n, adjust=False).mean() / a
    mdi = 100 * minus.ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean(), pdi, mdi


def _safe_div(a, b):
    return a / b.replace(0, np.nan) if isinstance(b, pd.Series) else a / (b if b else np.nan)


# --------------------------------------------------------------------------- #
# 종목별 전체 지표
# --------------------------------------------------------------------------- #
def compute(df: pd.DataFrame, cfg: TestConfig, market: Optional[pd.Series] = None,
            flows: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    o, h, l, c = (df[k].astype(float) for k in ("Open", "High", "Low", "Close"))
    v = df["Volume"].astype(float)
    d = pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c, "Volume": v}, index=df.index)
    n = len(d)

    # ---- 전고점 / 앵커 위치 ------------------------------------------------ #
    d["prior_high"] = h.shift(cfg.gap).rolling(cfg.lookback, min_periods=cfg.lookback // 2).max()
    d["dist"] = (d["prior_high"] - c) / d["prior_high"]
    anchor = np.full(n, np.nan)
    hv = h.values
    L = cfg.lookback
    if n >= L + cfg.gap:
        win = sliding_window_view(hv, L)          # win[i] = h[i : i+L]
        am = np.argmax(win, axis=1)               # 창 안에서 최고가 위치
        for t in range(L - 1 + cfg.gap, n):
            i = t - cfg.gap - L + 1
            anchor[t] = i + am[i]
    d["days_since_high"] = (np.arange(n) - anchor) / L

    # ---- Minervini 트렌드 템플릿 / Weinstein 스테이지 ---------------------- #
    ma20, ma50 = c.rolling(20).mean(), c.rolling(50).mean()
    ma150, ma200 = c.rolling(150).mean(), c.rolling(200).mean()
    hi252 = h.rolling(252, min_periods=200).max()
    lo252 = l.rolling(252, min_periods=200).min()
    conds = [c > ma150, c > ma200, ma150 > ma200, ma200 > ma200.shift(20),
             (ma50 > ma150) & (ma50 > ma200), c > ma50,
             (c >= lo252 * 1.30) & (c >= hi252 * 0.75)]
    d["trend"] = sum(x.astype(float) for x in conds) / len(conds)
    d.loc[ma200.isna(), "trend"] = np.nan
    d["hi52"] = c / hi252                          # 52주 신고가 근접도 (George & Hwang)
    d["disparity20"] = c / ma20 - 1                # 이격도

    # ---- ADX / DMI --------------------------------------------------------- #
    ax, pdi, mdi = adx(h, l, c)
    d["adx"] = ax
    d["dmi_bull"] = ((pdi > mdi) & (ax > 20)).astype(float)

    # ---- 일목균형표 -------------------------------------------------------- #
    tenkan = (h.rolling(9).max() + l.rolling(9).min()) / 2
    kijun = (h.rolling(26).max() + l.rolling(26).min()) / 2
    span_a = ((tenkan + kijun) / 2).shift(26)
    span_b = ((h.rolling(52).max() + l.rolling(52).min()) / 2).shift(26)
    cloud_top = pd.concat([span_a, span_b], axis=1).max(axis=1)
    d["ichimoku"] = ((c > cloud_top).astype(float) + (tenkan > kijun).astype(float)
                     + (c > c.shift(26)).astype(float)
                     + ((tenkan + kijun) / 2 > (h.rolling(52).max() + l.rolling(52).min()) / 2).astype(float)) / 4
    d.loc[span_b.isna(), "ichimoku"] = np.nan

    # ---- 상대강도 ---------------------------------------------------------- #
    d["rs_raw"] = (0.4 * c.pct_change(63) + 0.2 * c.pct_change(126)
                   + 0.2 * c.pct_change(189) + 0.2 * c.pct_change(252))
    d["ret20"] = c.pct_change(20)
    if market is not None:
        rsl = c / market.reindex(d.index).ffill()
        d["rsline_high"] = rsl / rsl.rolling(250, min_periods=120).max()   # 1이면 RS선 신고가
    else:
        d["rsline_high"] = np.nan

    # ---- 변동성 수축 (VCP, 스퀴즈, NR7) ------------------------------------ #
    r = c.pct_change()
    d["contraction"] = r.rolling(10).std() / r.rolling(50).std()
    d["range10"] = (h.rolling(10).max() - l.rolling(10).min()) / c
    d["vol_dryup"] = v.rolling(5).mean() / v.rolling(50).mean()
    std20 = c.rolling(20).std()
    atr20 = atr(h, l, c, 20)
    d["atr14"] = atr(h, l, c, 14)
    squeeze_on = (2 * std20 < 1.5 * atr20).astype(float)       # 볼린저밴드가 켈트너채널 안
    d["squeeze_days"] = squeeze_on.rolling(10).sum() / 10
    bbw = 4 * std20 / ma20
    d["bbw_pct"] = bbw.rolling(120, min_periods=60).rank(pct=True)   # 낮을수록 수축
    rng = h - l
    nr7 = (rng <= rng.rolling(7).min()).astype(float)
    d["nr7"] = nr7.rolling(3).max()
    d["atr_contract"] = d["atr14"] / d["atr14"].rolling(60).mean()

    # ---- 매집 / 수급 (가격·거래량) ----------------------------------------- #
    up, dn = c > c.shift(), c < c.shift()
    d["ud_ratio"] = _safe_div(v.where(up, 0).rolling(50).sum(), v.where(dn, 0).rolling(50).sum())
    obv = (np.sign(c.diff()).fillna(0) * v).cumsum()
    d["obv_slope"] = _safe_div(obv - obv.shift(20), v.rolling(20).sum())
    d["obv_div"] = d["obv_slope"] - d["ret20"].abs() * 2
    d["obv_high"] = obv / obv.rolling(120).max().where(lambda s: s > 0)   # OBV 선행 신고가
    mfm = _safe_div((c - l) - (h - c), h - l).fillna(0)
    d["cmf"] = _safe_div((mfm * v).rolling(20).sum(), v.rolling(20).sum())
    tp = (h + l + c) / 3
    mf = tp * v
    pos = mf.where(tp > tp.shift(), 0).rolling(14).sum()
    neg = mf.where(tp < tp.shift(), 0).rolling(14).sum()
    d["mfi"] = 100 - 100 / (1 + _safe_div(pos, neg))
    d["force"] = _safe_div((c.diff() * v).ewm(span=13, adjust=False).mean(), (c * v).rolling(20).mean())
    max_dn_vol10 = v.where(dn, 0).shift(1).rolling(10).max()
    d["pocket_pivots"] = (up & (v > max_dn_vol10)).astype(float).rolling(10).sum()
    d["vol_spike_up"] = ((v > 3 * v.rolling(50).mean().shift()) & up).astype(float).rolling(20).sum()
    d["clv5"] = mfm.rolling(5).mean()                               # 종가 위치(고가 마감 경향)
    d["upper_wick5"] = _safe_div(h - pd.concat([o, c], axis=1).max(axis=1), rng).rolling(5).mean()

    # ---- 매물대 (전고점까지 위쪽 대기 물량) / 앵커드 VWAP ------------------- #
    W = cfg.supply_window
    supply = np.full(n, np.nan)
    if n > W:
        tpw = sliding_window_view(tp.values, W)[:-1]     # tpw[i] = tp[i:i+W], 대상일 t = i+W
        vw = sliding_window_view(v.values, W)[:-1]
        cur = c.values[W:]
        phv = d["prior_high"].values[W:]
        m = (tpw > cur[:, None]) & (tpw <= phv[:, None])
        supply[W:] = (vw * m).sum(axis=1) / vw.sum(axis=1)
    d["overhead_supply"] = supply
    cpv, cv = np.cumsum((tp * v).values), np.cumsum(v.values)
    avwap = np.full(n, np.nan)
    for t in np.where(~np.isnan(anchor))[0]:
        a = int(anchor[t])
        pv = cpv[t] - (cpv[a - 1] if a > 0 else 0)
        vv = cv[t] - (cv[a - 1] if a > 0 else 0)
        avwap[t] = pv / vv if vv > 0 else np.nan
    d["avwap_gap"] = c / avwap - 1           # 양수: 고점 이후 매수자 평균 수익 구간(매도압력 약함)

    # ---- Darvas 박스 ------------------------------------------------------- #
    hi20, lo20 = h.rolling(20).max(), l.rolling(20).min()
    d["box"] = _safe_div(c - lo20, hi20 - lo20).fillna(0.5) * ((hi20 - lo20) / hi20 < 0.15).astype(float)
    d["touches60"] = ((d["dist"] >= 0) & (d["dist"] <= cfg.near)).astype(float).rolling(60, min_periods=1).sum() / 60

    d["rsi14"] = rsi(c, 14)
    d["liquidity"] = (c * v).rolling(20).mean()

    # ---- 투자자 수급 (pykrx, 선택) ----------------------------------------- #
    if flows is not None and not flows.empty:
        f = flows.reindex(d.index)
        tv = (c * v).rolling(20).sum()
        net = f.get("foreign", 0).fillna(0) + f.get("institution", 0).fillna(0)
        d["flow20"] = _safe_div(net.rolling(20).sum(), tv)
        fpos = (f.get("foreign", pd.Series(0, index=d.index)).fillna(0) > 0).astype(int)
        streak = fpos.groupby((fpos == 0).cumsum()).cumsum()
        d["foreign_streak"] = streak.clip(upper=10) / 10
        if "short_ratio" in f:
            d["short_chg20"] = -(f["short_ratio"].ffill() - f["short_ratio"].ffill().shift(20))
        else:
            d["short_chg20"] = np.nan
    else:
        d["flow20"] = d["foreign_streak"] = d["short_chg20"] = np.nan

    # ---- 라벨: t+1..t+k 고가가 전고점 돌파 --------------------------------- #
    target = d["prior_high"] * (1 + cfg.margin)
    for k in sorted({1, 2, 3, cfg.days}):
        fh = pd.concat([h.shift(-j) for j in range(1, k + 1)], axis=1)
        lab = (fh.max(axis=1) > target).astype(float)
        lab[fh.isna().any(axis=1) | target.isna()] = np.nan
        d[f"hit{k}"] = lab
    d["hit"] = d[f"hit{cfg.days}"]

    simulate_trades(d, cfg)
    return d


def simulate_trades(d: pd.DataFrame, cfg: TestConfig) -> None:
    """t+1 시가 진입. 같은 날 손절·목표 동시 터치 시 손절 우선(보수적).
    익절가(전고점×(1+take_profit)) 도달 → 익절가 청산, 이후 날 시가가 목표 위 → 시가 청산,
    손절가 이탈 → 손절가(시가가 더 낮으면 시가) 청산, 끝까지 없으면 N일째 종가 청산.
    시가가 이미 전고점 위면(갭 돌파) 매매하지 않음(pnl NaN, 돌파 라벨은 유지)."""
    o, h, l, c = (d[k].values for k in ("Open", "High", "Low", "Close"))
    n = len(d)
    tgt = (d["prior_high"] * (1 + cfg.margin)).values
    xt = (d["prior_high"] * (1 + cfg.take_profit)).values   # 익절가
    at = d["atr14"].values
    pnl = np.full(n, np.nan)
    gap = np.full(n, np.nan)
    stopped = np.full(n, np.nan)
    for t in range(n - cfg.days):
        if np.isnan(tgt[t]):
            continue
        e = o[t + 1]
        if e >= tgt[t]:
            gap[t] = 1.0
            continue
        gap[t] = 0.0
        stop = e - cfg.stop_atr * at[t] if cfg.stop_atr > 0 and not np.isnan(at[t]) else -np.inf
        px, st = None, 0.0
        for k in range(1, cfg.days + 1):
            i = t + k
            if k > 1 and o[i] >= xt[t]:
                px = o[i]; break
            if k > 1 and o[i] <= stop:
                px, st = o[i], 1.0; break
            if l[i] <= stop:
                px, st = stop, 1.0; break
            if h[i] > xt[t]:
                px = xt[t]; break
        if px is None:
            px = c[t + cfg.days]
        pnl[t] = px / e - 1 - cfg.cost
        stopped[t] = st
    d["pnl"], d["gap_entry"], d["stopped"] = pnl, gap, stopped


# 모델/리포트에 쓰는 지표 목록: (컬럼, 높을수록 좋은가) - 부호는 규칙 점수용
FEATURES = [
    ("dist", False), ("days_since_high", True), ("touches60", True),
    ("trend", True), ("hi52", True), ("disparity20", True), ("adx", True), ("dmi_bull", True),
    ("ichimoku", True), ("rs_raw", True), ("ret20", True), ("rsline_high", True),
    ("contraction", False), ("range10", False), ("vol_dryup", False), ("squeeze_days", True),
    ("bbw_pct", False), ("nr7", True), ("atr_contract", False),
    ("ud_ratio", True), ("obv_slope", True), ("obv_div", True), ("obv_high", True), ("cmf", True),
    ("mfi", True), ("force", True), ("pocket_pivots", True), ("vol_spike_up", True), ("clv5", True),
    ("upper_wick5", False), ("overhead_supply", False), ("avwap_gap", True), ("box", True),
    ("rsi14", True), ("flow20", True), ("foreign_streak", True), ("short_chg20", True),
]
