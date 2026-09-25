#!/usr/bin/env python3
"""
전고점 돌파 확률 발굴기 (Prior-High Breakout Probability Finder)

각 종목의 과거 데이터에서 "전고점 근처 접근" 이벤트를 수집하고, 그 이벤트가
이후 N일 안에 실제로 전고점을 돌파했는지를 라벨로 삼아 로지스틱 회귀 모델을
학습한다. 학습된 모델로 "현재 전고점 근처에 있는 종목"의 돌파 확률을 추정하고,
기준 확률(기본 60%) 이상인 종목만 골라낸다.

사용 예:
  python breakout_finder.py                       # 기본 한국 유니버스, 60% 이상
  python breakout_finder.py --market us           # 미국 유니버스
  python breakout_finder.py --tickers 005930.KS 000660.KS
  python breakout_finder.py --universe-file my.txt --min-prob 0.65
  python breakout_finder.py --csv-dir ./ohlcv     # 오프라인 CSV (Date,Open,High,Low,Close,Volume)

데이터 소스: yfinance (인터넷 필요). 오프라인은 --csv-dir 사용.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
# 기본 유니버스
# --------------------------------------------------------------------------- #
KR_UNIVERSE = {
    "005930.KS": "삼성전자", "000660.KS": "SK하이닉스", "373220.KS": "LG에너지솔루션",
    "207940.KS": "삼성바이오로직스", "005380.KS": "현대차", "000270.KS": "기아",
    "068270.KS": "셀트리온", "005490.KS": "POSCO홀딩스", "035420.KS": "NAVER",
    "051910.KS": "LG화학", "006400.KS": "삼성SDI", "035720.KS": "카카오",
    "028260.KS": "삼성물산", "012330.KS": "현대모비스", "105560.KS": "KB금융",
    "055550.KS": "신한지주", "066570.KS": "LG전자", "003670.KS": "포스코퓨처엠",
    "096770.KS": "SK이노베이션", "032830.KS": "삼성생명", "086790.KS": "하나금융지주",
    "017670.KS": "SK텔레콤", "034730.KS": "SK", "015760.KS": "한국전력",
    "003550.KS": "LG", "009150.KS": "삼성전기", "033780.KS": "KT&G",
    "010130.KS": "고려아연", "018260.KS": "삼성에스디에스", "011200.KS": "HMM",
    "329180.KS": "HD현대중공업", "042660.KS": "한화오션", "009540.KS": "HD한국조선해양",
    "012450.KS": "한화에어로스페이스", "047050.KS": "포스코인터내셔널", "030200.KS": "KT",
    "000810.KS": "삼성화재", "316140.KS": "우리금융지주", "138040.KS": "메리츠금융지주",
    "010950.KS": "S-Oil", "024110.KS": "기업은행", "090430.KS": "아모레퍼시픽",
    "051900.KS": "LG생활건강", "402340.KS": "SK스퀘어", "011070.KS": "LG이노텍",
    "267250.KS": "HD현대", "064350.KS": "현대로템", "005070.KS": "코스모신소재",
    "000100.KS": "유한양행", "302440.KS": "SK바이오사이언스", "326030.KS": "SK바이오팜",
    "247540.KQ": "에코프로비엠", "086520.KQ": "에코프로", "028300.KQ": "HLB",
    "196170.KQ": "알테오젠", "066970.KQ": "엘앤에프", "403870.KQ": "HPSP",
    "041510.KQ": "에스엠", "035900.KQ": "JYP Ent.", "263750.KQ": "펄어비스",
    "293490.KQ": "카카오게임즈", "058470.KQ": "리노공업", "039030.KQ": "이오테크닉스",
    "112040.KQ": "위메이드", "214150.KQ": "클래시스", "145020.KQ": "휴젤",
}

US_UNIVERSE = {
    "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA", "AMZN": "Amazon",
    "GOOGL": "Alphabet", "META": "Meta", "TSLA": "Tesla", "AVGO": "Broadcom",
    "BRK-B": "Berkshire", "LLY": "Eli Lilly", "JPM": "JPMorgan", "V": "Visa",
    "UNH": "UnitedHealth", "XOM": "Exxon", "MA": "Mastercard", "COST": "Costco",
    "JNJ": "J&J", "PG": "P&G", "HD": "Home Depot", "NFLX": "Netflix",
    "ABBV": "AbbVie", "BAC": "BofA", "CRM": "Salesforce", "ORCL": "Oracle",
    "AMD": "AMD", "KO": "Coca-Cola", "CVX": "Chevron", "MRK": "Merck",
    "PEP": "PepsiCo", "ADBE": "Adobe", "WMT": "Walmart", "TMO": "Thermo Fisher",
    "ACN": "Accenture", "CSCO": "Cisco", "LIN": "Linde", "MCD": "McDonald's",
    "ABT": "Abbott", "INTU": "Intuit", "QCOM": "Qualcomm", "TXN": "TI",
    "AMAT": "Applied Materials", "GE": "GE Aerospace", "CAT": "Caterpillar",
    "ISRG": "Intuitive", "NOW": "ServiceNow", "BKNG": "Booking", "UBER": "Uber",
    "PLTR": "Palantir", "MU": "Micron", "LRCX": "Lam Research",
}

# --------------------------------------------------------------------------- #
# 설정
# --------------------------------------------------------------------------- #
@dataclass
class Config:
    lookback: int = 120       # 전고점 산정 기간(거래일)
    gap: int = 5              # 최근 gap일은 전고점 산정에서 제외 (지금 고점 = 전고점 방지)
    near_pct: float = 0.05    # 전고점까지 거리가 이 비율 이내면 "접근 이벤트"
    horizon: int = 20         # 돌파 여부 판정 기간(거래일)
    margin: float = 0.0       # 전고점 * (1+margin) 이상 찍어야 돌파로 인정
    min_prob: float = 0.60    # 발굴 기준 확률
    min_events_per_ticker: int = 5
    event_cooldown: int = 5   # 같은 종목에서 이벤트 최소 간격(중복 샘플 완화)
    period: str = "5y"        # yfinance 다운로드 기간
    l2: float = 1.0           # 로지스틱 회귀 L2 정규화


FEATURE_NAMES = [
    "dist_to_high",      # (H - close)/H, 작을수록 고점 근접
    "vol_ratio_20_60",   # 최근 20일 거래량 / 60일 거래량
    "vol_ratio_5_20",    # 최근 5일 거래량 / 20일 거래량
    "rsi14",
    "close_vs_ma20",
    "close_vs_ma60",
    "ma20_slope",        # (MA20 / MA20 10일전 - 1)
    "ma20_over_ma60",
    "volatility20",      # 20일 일간수익률 표준편차
    "touches60",         # 최근 60일간 전고점 근접(near_pct 이내) 일수
    "days_since_high",   # 전고점 이후 경과 거래일 / lookback
    "ret20",
    "ret60",
    "range_pos20",       # 20일 레인지 내 종가 위치 (0~1)
    "high_age_ratio",    # 전고점이 lookback 대비 얼마나 오래됐나
]


# --------------------------------------------------------------------------- #
# 지표 계산
# --------------------------------------------------------------------------- #
def rsi(series: pd.Series, n: int = 14) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)
    ru = up.ewm(alpha=1 / n, adjust=False).mean()
    rd = down.ewm(alpha=1 / n, adjust=False).mean()
    rs = ru / rd.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def compute_features(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """OHLCV 데이터프레임에 피처/전고점/라벨 컬럼을 추가해 반환."""
    d = df.copy()
    c, h, v = d["Close"], d["High"], d["Volume"].astype(float)

    # 전고점: 최근 gap일을 제외한 lookback 기간 최고가
    prior_high = h.shift(cfg.gap).rolling(cfg.lookback, min_periods=cfg.lookback // 2).max()
    d["prior_high"] = prior_high
    d["dist_to_high"] = (prior_high - c) / prior_high

    # 전고점 위치(며칠 전인지)
    win = cfg.lookback + cfg.gap
    def _argmax_age(x: np.ndarray) -> float:
        x = x[: len(x) - cfg.gap] if cfg.gap > 0 else x
        if len(x) == 0 or np.all(np.isnan(x)):
            return np.nan
        return float(len(x) - 1 - int(np.nanargmax(x))) + cfg.gap
    d["days_since_high"] = h.rolling(win, min_periods=win // 2).apply(_argmax_age, raw=True)
    d["high_age_ratio"] = d["days_since_high"] / win
    d["days_since_high"] = d["days_since_high"] / cfg.lookback

    v20, v60, v5 = v.rolling(20).mean(), v.rolling(60).mean(), v.rolling(5).mean()
    d["vol_ratio_20_60"] = (v20 / v60.replace(0, np.nan)).fillna(1.0)
    d["vol_ratio_5_20"] = (v5 / v20.replace(0, np.nan)).fillna(1.0)
    d["rsi14"] = rsi(c, 14) / 100.0

    ma20, ma60 = c.rolling(20).mean(), c.rolling(60).mean()
    d["close_vs_ma20"] = c / ma20 - 1
    d["close_vs_ma60"] = c / ma60 - 1
    d["ma20_slope"] = ma20 / ma20.shift(10) - 1
    d["ma20_over_ma60"] = ma20 / ma60 - 1
    d["volatility20"] = c.pct_change().rolling(20).std()
    near = (d["dist_to_high"] <= cfg.near_pct) & (d["dist_to_high"] >= -0.0)
    d["touches60"] = near.astype(float).rolling(60, min_periods=1).sum() / 60.0
    d["ret20"] = c / c.shift(20) - 1
    d["ret60"] = c / c.shift(60) - 1
    lo20, hi20 = d["Low"].rolling(20).min(), h.rolling(20).max()
    d["range_pos20"] = ((c - lo20) / (hi20 - lo20).replace(0, np.nan)).fillna(0.5)

    # 라벨: 향후 horizon일 내 최고가가 prior_high*(1+margin) 초과
    fut_max = h.shift(-1).rolling(cfg.horizon, min_periods=cfg.horizon).max().shift(-(cfg.horizon - 1))
    d["fut_max"] = fut_max
    d["label"] = (fut_max > prior_high * (1 + cfg.margin)).astype(float)
    d.loc[fut_max.isna(), "label"] = np.nan

    # 이벤트 조건: 전고점 아래 near_pct 이내 (이미 돌파한 상태는 제외)
    d["is_event"] = (d["dist_to_high"] >= 0) & (d["dist_to_high"] <= cfg.near_pct)
    return d


def extract_events(d: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """쿨다운을 적용해 이벤트 행만 추출."""
    ev = d[d["is_event"]].dropna(subset=FEATURE_NAMES)
    if ev.empty:
        return ev
    keep, last_pos = [], -10**9
    pos_index = {ts: i for i, ts in enumerate(d.index)}
    for ts in ev.index:
        p = pos_index[ts]
        if p - last_pos >= cfg.event_cooldown:
            keep.append(ts)
            last_pos = p
    return ev.loc[keep]


# --------------------------------------------------------------------------- #
# 로지스틱 회귀 (numpy)
# --------------------------------------------------------------------------- #
class LogisticModel:
    def __init__(self, l2: float = 1.0, iters: int = 3000, lr: float = 0.1):
        self.l2, self.iters, self.lr = l2, iters, lr
        self.mu = self.sd = self.w = None
        self.b = 0.0

    def _z(self, X: np.ndarray) -> np.ndarray:
        return (X - self.mu) / self.sd

    def fit(self, X: np.ndarray, y: np.ndarray) -> "LogisticModel":
        self.mu = X.mean(axis=0)
        self.sd = X.std(axis=0) + 1e-9
        Z = self._z(X)
        n, k = Z.shape
        self.w = np.zeros(k)
        self.b = float(np.log((y.mean() + 1e-6) / (1 - y.mean() + 1e-6)))
        for _ in range(self.iters):
            p = 1 / (1 + np.exp(-(Z @ self.w + self.b)))
            g = p - y
            gw = Z.T @ g / n + self.l2 * self.w / n
            gb = g.mean()
            self.w -= self.lr * gw
            self.b -= self.lr * gb
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        return 1 / (1 + np.exp(-(self._z(X) @ self.w + self.b)))


def auc_score(y: np.ndarray, p: np.ndarray) -> float:
    # 동점 평균 랭크 기반 Mann-Whitney AUC
    s = pd.Series(p).rank(method="average").values
    n1 = y.sum()
    n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return float("nan")
    return float((s[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def calibration_table(y: np.ndarray, p: np.ndarray, bins: int = 5) -> List[dict]:
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for i in range(bins):
        m = (p >= edges[i]) & (p < edges[i + 1] if i < bins - 1 else p <= edges[i + 1])
        if m.sum() == 0:
            continue
        out.append({"bin": f"{edges[i]:.1f}-{edges[i+1]:.1f}", "n": int(m.sum()),
                    "pred": round(float(p[m].mean()), 3), "actual": round(float(y[m].mean()), 3)})
    return out


# --------------------------------------------------------------------------- #
# 데이터 로딩
# --------------------------------------------------------------------------- #
def load_from_yfinance(tickers: List[str], period: str) -> Dict[str, pd.DataFrame]:
    try:
        import yfinance as yf
    except ImportError:
        sys.exit("yfinance 미설치: pip install yfinance")
    out: Dict[str, pd.DataFrame] = {}
    raw = yf.download(tickers, period=period, group_by="ticker", auto_adjust=True,
                      progress=False, threads=True)
    if raw is None or raw.empty:
        return out
    for t in tickers:
        try:
            df = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        df = df.dropna(subset=["Close"])
        if len(df) >= 200:
            out[t] = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    return out


def load_from_csv_dir(csv_dir: str, tickers: Optional[List[str]]) -> Dict[str, pd.DataFrame]:
    out: Dict[str, pd.DataFrame] = {}
    for fn in sorted(os.listdir(csv_dir)):
        if not fn.lower().endswith(".csv"):
            continue
        t = fn[:-4]
        if tickers and t not in tickers:
            continue
        df = pd.read_csv(os.path.join(csv_dir, fn), parse_dates=["Date"]).set_index("Date").sort_index()
        need = {"Open", "High", "Low", "Close", "Volume"}
        if not need.issubset(df.columns):
            print(f"[skip] {fn}: 컬럼 부족 {need - set(df.columns)}", file=sys.stderr)
            continue
        if len(df) >= 200:
            out[t] = df[list(need)].astype(float)
    return out


# --------------------------------------------------------------------------- #
# 메인 파이프라인
# --------------------------------------------------------------------------- #
@dataclass
class Candidate:
    ticker: str
    name: str
    date: str
    close: float
    prior_high: float
    dist_to_high_pct: float
    prob_model: float
    prob_ticker_base: float
    prob_blend: float
    n_ticker_events: int
    vol_ratio_20_60: float
    rsi14: float
    touches60: int


def run(data: Dict[str, pd.DataFrame], names: Dict[str, str], cfg: Config,
        verbose: bool = True) -> Tuple[List[Candidate], dict]:
    feats: Dict[str, pd.DataFrame] = {}
    ev_frames = []
    for t, df in data.items():
        d = compute_features(df, cfg)
        feats[t] = d
        ev = extract_events(d, cfg)
        if ev.empty:
            continue
        ev = ev.assign(ticker=t)
        ev_frames.append(ev)
    if not ev_frames:
        return [], {"error": "이벤트 없음"}

    events = pd.concat(ev_frames)
    labeled = events.dropna(subset=["label"])
    if len(labeled) < 50:
        return [], {"error": f"학습 이벤트 부족: {len(labeled)}"}

    X = labeled[FEATURE_NAMES].values.astype(float)
    y = labeled["label"].values.astype(float)

    # 시간순 검증: 앞 70% 학습 / 뒤 30% 검증
    labeled_sorted = labeled.sort_index()
    cut = int(len(labeled_sorted) * 0.7)
    tr, te = labeled_sorted.iloc[:cut], labeled_sorted.iloc[cut:]
    m_val = LogisticModel(l2=cfg.l2).fit(tr[FEATURE_NAMES].values.astype(float), tr["label"].values)
    p_te = m_val.predict_proba(te[FEATURE_NAMES].values.astype(float))
    y_te = te["label"].values
    hi = p_te >= cfg.min_prob
    report = {
        "n_events_total": int(len(labeled)),
        "base_rate_all": round(float(y.mean()), 3),
        "validation": {
            "n_train": int(len(tr)), "n_test": int(len(te)),
            "auc": round(auc_score(y_te, p_te), 3),
            "test_base_rate": round(float(y_te.mean()), 3),
            f"actual_rate_when_pred>={cfg.min_prob:.2f}": (round(float(y_te[hi].mean()), 3) if hi.sum() else None),
            f"n_pred>={cfg.min_prob:.2f}": int(hi.sum()),
            "calibration": calibration_table(y_te, p_te),
        },
    }

    # 최종 모델: 전체 라벨 데이터로 재학습
    model = LogisticModel(l2=cfg.l2).fit(X, y)
    report["feature_weights"] = {n: round(float(w), 3) for n, w in
                                 sorted(zip(FEATURE_NAMES, model.w), key=lambda z: -abs(z[1]))}

    # 종목별 과거 돌파 기저율
    base_by_ticker = labeled.groupby("ticker")["label"].agg(["mean", "count"])

    # 현재 시점 후보: 마지막 봉이 이벤트 조건을 만족
    cands: List[Candidate] = []
    for t, d in feats.items():
        last = d.iloc[-1]
        if not bool(last["is_event"]) or last[FEATURE_NAMES].isna().any():
            continue
        p_model = float(model.predict_proba(last[FEATURE_NAMES].values.astype(float)[None, :])[0])
        if t in base_by_ticker.index:
            b_mean, b_n = float(base_by_ticker.loc[t, "mean"]), int(base_by_ticker.loc[t, "count"])
        else:
            b_mean, b_n = float(y.mean()), 0
        # 종목 기저율은 표본 수에 따라 가중 (베이지안 수축)
        k = 10.0
        b_shrunk = (b_mean * b_n + float(y.mean()) * k) / (b_n + k)
        p_blend = 0.7 * p_model + 0.3 * b_shrunk
        cands.append(Candidate(
            ticker=t, name=names.get(t, t), date=str(d.index[-1].date()),
            close=round(float(last["Close"]), 2), prior_high=round(float(last["prior_high"]), 2),
            dist_to_high_pct=round(float(last["dist_to_high"]) * 100, 2),
            prob_model=round(p_model, 3), prob_ticker_base=round(b_shrunk, 3),
            prob_blend=round(p_blend, 3), n_ticker_events=b_n,
            vol_ratio_20_60=round(float(last["vol_ratio_20_60"]), 2),
            rsi14=round(float(last["rsi14"]) * 100, 1),
            touches60=int(round(float(last["touches60"]) * 60)),
        ))
    cands.sort(key=lambda c: -c.prob_blend)
    return cands, report


def print_report(cands: List[Candidate], report: dict, cfg: Config) -> None:
    print("=" * 78)
    print(f" 전고점 돌파 확률 발굴기  |  기준 {cfg.min_prob*100:.0f}%  |  전고점 {cfg.lookback}일  "
          f"|  판정 {cfg.horizon}일  |  근접 {cfg.near_pct*100:.0f}%")
    print("=" * 78)
    if "error" in report:
        print("오류:", report["error"]); return
    v = report["validation"]
    print(f"학습 이벤트 {report['n_events_total']}건, 전체 돌파율 {report['base_rate_all']*100:.1f}%")
    print(f"시간순 검증(뒤 30%): AUC {v['auc']}, 검증 기저율 {v['test_base_rate']*100:.1f}%, "
          f"예측≥{cfg.min_prob:.2f} 구간 실제 돌파율 "
          f"{(v[f'actual_rate_when_pred>={cfg.min_prob:.2f}'] or 0)*100:.1f}% "
          f"(n={v[f'n_pred>={cfg.min_prob:.2f}']})")
    print("캘리브레이션:", ", ".join(f"{c['bin']}: 예측 {c['pred']:.2f}/실제 {c['actual']:.2f} (n={c['n']})"
                                for c in v["calibration"]))
    print("주요 피처 가중치:", ", ".join(f"{k}={w:+.2f}" for k, w in list(report["feature_weights"].items())[:6]))
    print("-" * 78)
    picks = [c for c in cands if c.prob_blend >= cfg.min_prob]
    print(f"현재 전고점 근접 종목 {len(cands)}개 중 확률 {cfg.min_prob*100:.0f}% 이상: {len(picks)}개\n")
    hdr = f"{'티커':<11}{'종목명':<14}{'종가':>12}{'전고점':>12}{'이격%':>7}{'모델':>7}{'기저':>7}{'종합':>7}{'거래량비':>9}{'RSI':>6}{'터치':>5}"
    print(hdr); print("-" * len(hdr))
    for c in (picks or cands[:10]):
        print(f"{c.ticker:<11}{c.name[:12]:<14}{c.close:>12,.0f}{c.prior_high:>12,.0f}{c.dist_to_high_pct:>7.2f}"
              f"{c.prob_model*100:>6.0f}%{c.prob_ticker_base*100:>6.0f}%{c.prob_blend*100:>6.0f}%"
              f"{c.vol_ratio_20_60:>9.2f}{c.rsi14:>6.0f}{c.touches60:>5}")
    if not picks:
        print("\n(기준 이상 종목 없음. 참고로 상위 10개를 표시했습니다.)")
    print("\n※ 과거 통계 기반 추정치이며 투자 권유가 아닙니다. 검증 AUC/캘리브레이션을 함께 확인하세요.")


def main() -> None:
    ap = argparse.ArgumentParser(description="전고점 돌파 확률 60% 이상 종목 발굴")
    ap.add_argument("--market", choices=["kr", "us"], default="kr")
    ap.add_argument("--tickers", nargs="*", help="직접 지정 티커 (예: 005930.KS AAPL)")
    ap.add_argument("--universe-file", help="티커 목록 파일 (한 줄에 '티커[,이름]')")
    ap.add_argument("--csv-dir", help="오프라인 OHLCV CSV 디렉터리 (파일명=티커.csv)")
    ap.add_argument("--period", default="5y")
    ap.add_argument("--lookback", type=int, default=120)
    ap.add_argument("--horizon", type=int, default=20)
    ap.add_argument("--near", type=float, default=0.05)
    ap.add_argument("--margin", type=float, default=0.0)
    ap.add_argument("--min-prob", type=float, default=0.60)
    ap.add_argument("--out", default="breakout_candidates")
    ap.add_argument("--no-files", action="store_true", help="CSV/JSON 저장 안 함")
    a = ap.parse_args()

    cfg = Config(lookback=a.lookback, horizon=a.horizon, near_pct=a.near,
                 margin=a.margin, min_prob=a.min_prob, period=a.period)

    names: Dict[str, str] = dict(KR_UNIVERSE if a.market == "kr" else US_UNIVERSE)
    if a.universe_file:
        names = {}
        with open(a.universe_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [p.strip() for p in line.split(",")]
                names[parts[0]] = parts[1] if len(parts) > 1 else parts[0]
    if a.tickers:
        names = {t: names.get(t, t) for t in a.tickers}
    tickers = list(names)

    if a.csv_dir:
        data = load_from_csv_dir(a.csv_dir, tickers if (a.tickers or a.universe_file) else None)
        for t in data:
            names.setdefault(t, t)
    else:
        print(f"{len(tickers)}개 종목 데이터 다운로드 중 (yfinance, {cfg.period})...", file=sys.stderr)
        data = load_from_yfinance(tickers, cfg.period)
    if not data:
        sys.exit("데이터를 불러오지 못했습니다. 네트워크/티커/CSV 경로를 확인하세요.")
    print(f"{len(data)}개 종목 로드 완료", file=sys.stderr)

    cands, report = run(data, names, cfg)
    print_report(cands, report, cfg)

    if not a.no_files and cands:
        pd.DataFrame([asdict(c) for c in cands]).to_csv(f"{a.out}.csv", index=False, encoding="utf-8-sig")
        with open(f"{a.out}.json", "w", encoding="utf-8") as f:
            json.dump({"generated": datetime.now().isoformat(timespec="seconds"), "config": asdict(cfg),
                       "report": report, "candidates": [asdict(c) for c in cands]}, f, ensure_ascii=False, indent=2)
        print(f"\n저장: {a.out}.csv, {a.out}.json")


if __name__ == "__main__":
    main()
