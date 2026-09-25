#!/usr/bin/env python3
"""
자동매매 준비 모듈 (모의 → 한국투자증권 모의투자 → 실전 순서로 단계적으로)

명령
  plan     장 마감 후(16:10). 전략으로 다음 날 매수 계획을 만든다 (state/plan_YYYYMMDD.json)
  enter    09:30~10:00. 계획 종목이 아직 전고점 아래면 지정가 매수
  monitor  장중 5분마다. 보유 종목 익절가·손절가 확인 후 매도
  close    15:20. 보유기간이 끝난 종목을 종가 동시호가에 매도
  status   예수금, 보유 종목, 오늘 손익

모드 (autotrade_config.json 의 "mode")
  paper     내부 모의매매. 증권사 연결 없음. 기본값
  kis_mock  한국투자증권 모의투자 계좌 (openapivts)
  kis_live  실전. "allow_live": true 와 환경변수 AUTOTRADE_LIVE_CONFIRM=YES 가 모두 있어야 동작

안전장치
  - state 폴더에 STOP 파일이 있으면 신규 매수 중단 (매도는 허용)
  - 하루 손실이 한도를 넘으면 STOP 파일을 자동 생성
  - 1회 주문 금액 상한, 동시 보유 종목 수 상한, 같은 날 같은 종목 중복 매수 금지
  - 허용 시간대 밖에서는 주문하지 않음 (--force 로 테스트 가능)
  - 모든 주문을 orders.csv 에 기록

한국투자증권 연결: 환경변수 KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT(예 12345678-01)
tr_id 는 증권사 개편 시 바뀔 수 있으니 config 의 "kis" 항목을 KIS 개발자센터 문서와 맞춰 두세요.

crontab 예시 (평일, 서울 시간)
  10 16 * * 1-5  python autotrade.py plan
  32 9  * * 1-5  python autotrade.py enter
  */5 9-15 * * 1-5 python autotrade.py monitor
  21 15 * * 1-5  python autotrade.py close
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

DEFAULT_CONFIG = {
    "mode": "paper",
    "allow_live": False,
    "strategy": "ml",
    "min_prob": 0.60,
    "days": 3,
    "take_profit": 0.03,
    "stop_atr": 2.0,
    "market": "kr",
    "picks": 3,
    "capital_per_trade": 1_000_000,
    "max_order_krw": 3_000_000,
    "max_positions": 3,
    "max_daily_loss_pct": 2.0,
    "max_gap_pct": 3.0,
    "entry_window": ["09:30", "10:00"],
    "monitor_window": ["09:30", "15:15"],
    "close_window": ["15:20", "15:29"],
    "paper_cash": 10_000_000,
    "state_dir": "autotrade_state",
    "kis": {
        "real_url": "https://openapi.koreainvestment.com:9443",
        "mock_url": "https://openapivts.koreainvestment.com:29443",
        "tr_buy": "TTTC0802U", "tr_sell": "TTTC0801U", "tr_balance": "TTTC8434R",
        "tr_buy_mock": "VTTC0802U", "tr_sell_mock": "VTTC0801U", "tr_balance_mock": "VTTC8434R",
        "tr_price": "FHKST01010100",
    },
}


def load_config(path: str) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
        kis = {**cfg["kis"], **user.get("kis", {})}
        cfg.update(user)
        cfg["kis"] = kis
    return cfg


# --------------------------------------------------------------------------- #
# 가격 단위 (2023년 이후 코스피·코스닥 공통 호가단위)
# --------------------------------------------------------------------------- #
def tick_size(p: float) -> int:
    for lim, t in ((2000, 1), (5000, 5), (20000, 10), (50000, 50), (200000, 100), (500000, 500)):
        if p < lim:
            return t
    return 1000


def round_tick(p: float, up: bool) -> int:
    t = tick_size(p)
    return int(math.ceil(p / t) * t) if up else int(math.floor(p / t) * t)


# --------------------------------------------------------------------------- #
# 브로커
# --------------------------------------------------------------------------- #
class Broker:
    def price(self, code: str) -> float: ...
    def buy(self, code: str, qty: int, price: Optional[int]) -> dict: ...
    def sell(self, code: str, qty: int, price: Optional[int]) -> dict: ...
    def balance(self) -> dict: ...


class PaperBroker(Broker):
    """내부 모의매매. 가격은 prices 딕셔너리(테스트용) → 일봉 CSV 마지막 종가 → yfinance 순서로 찾는다."""

    def __init__(self, state_dir: str, cash: float, csv_dir: Optional[str] = None,
                 prices: Optional[Dict[str, float]] = None):
        self.path = os.path.join(state_dir, "paper_account.json")
        self.csv_dir, self.prices = csv_dir, prices or {}
        if os.path.exists(self.path):
            with open(self.path) as f:
                self.acct = json.load(f)
        else:
            self.acct = {"cash": cash, "holdings": {}}
            self._save()

    def _save(self):
        with open(self.path, "w") as f:
            json.dump(self.acct, f, ensure_ascii=False, indent=1)

    def price(self, code: str) -> float:
        if code in self.prices:
            return float(self.prices[code])
        if self.csv_dir:
            for suf in (".KS", ".KQ", ""):
                p = os.path.join(self.csv_dir, f"{code}{suf}.csv")
                if os.path.exists(p):
                    return float(pd.read_csv(p)["Close"].iloc[-1])
        import yfinance as yf
        for suf in (".KS", ".KQ"):
            h = yf.Ticker(code + suf).history(period="1d")
            if not h.empty:
                return float(h["Close"].iloc[-1])
        raise RuntimeError(f"{code} 가격을 찾지 못했습니다")

    def buy(self, code, qty, price):
        px = price or self.price(code)
        cost = px * qty * 1.00015
        if cost > self.acct["cash"]:
            return {"ok": False, "msg": "예수금 부족"}
        self.acct["cash"] -= cost
        h = self.acct["holdings"].get(code, {"qty": 0, "avg": 0})
        h["avg"] = (h["avg"] * h["qty"] + px * qty) / (h["qty"] + qty)
        h["qty"] += qty
        self.acct["holdings"][code] = h
        self._save()
        return {"ok": True, "price": px, "qty": qty, "order_no": f"P{int(time.time()*1000)}"}

    def sell(self, code, qty, price):
        h = self.acct["holdings"].get(code)
        if not h or h["qty"] < qty:
            return {"ok": False, "msg": "보유 수량 부족"}
        px = price or self.price(code)
        self.acct["cash"] += px * qty * (1 - 0.00015 - 0.0018)   # 수수료 + 거래세(근사)
        h["qty"] -= qty
        if h["qty"] == 0:
            del self.acct["holdings"][code]
        self._save()
        return {"ok": True, "price": px, "qty": qty, "order_no": f"P{int(time.time()*1000)}"}

    def balance(self):
        hold = {c: {**h, "price": self.price(c)} for c, h in self.acct["holdings"].items()}
        value = sum(h["qty"] * h["price"] for h in hold.values())
        return {"cash": self.acct["cash"], "holdings": hold, "total": self.acct["cash"] + value}


class KisBroker(Broker):
    """한국투자증권 Open API (REST)."""

    def __init__(self, cfg: dict, live: bool, state_dir: str):
        import requests
        self.requests = requests
        self.live, self.k = live, cfg["kis"]
        self.base = self.k["real_url"] if live else self.k["mock_url"]
        self.app_key, self.app_secret = os.environ.get("KIS_APP_KEY"), os.environ.get("KIS_APP_SECRET")
        acct = os.environ.get("KIS_ACCOUNT", "")
        if not (self.app_key and self.app_secret and "-" in acct):
            raise SystemExit("KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCOUNT(12345678-01 형식) 환경변수가 필요합니다.")
        self.cano, self.prdt = acct.split("-")
        self.token_path = os.path.join(state_dir, f"kis_token_{'live' if live else 'mock'}.json")

    def _tr(self, name: str) -> str:
        return self.k[name] if self.live else self.k[name + "_mock"]

    def token(self) -> str:
        if os.path.exists(self.token_path):
            with open(self.token_path) as f:
                t = json.load(f)
            if datetime.fromisoformat(t["expires"]) > datetime.now() + timedelta(minutes=10):
                return t["access_token"]
        r = self.requests.post(f"{self.base}/oauth2/tokenP", json={
            "grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret}, timeout=10).json()
        if "access_token" not in r:
            raise RuntimeError(f"토큰 발급 실패: {r}")
        exp = datetime.now() + timedelta(seconds=int(r.get("expires_in", 86400)))
        with open(self.token_path, "w") as f:
            json.dump({"access_token": r["access_token"], "expires": exp.isoformat()}, f)
        return r["access_token"]

    def _headers(self, tr_id: str) -> dict:
        return {"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {self.token()}",
                "appkey": self.app_key, "appsecret": self.app_secret, "tr_id": tr_id, "custtype": "P"}

    def price(self, code: str) -> float:
        r = self.requests.get(f"{self.base}/uapi/domestic-stock/v1/quotations/inquire-price",
                              headers=self._headers(self.k["tr_price"]),
                              params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}, timeout=10).json()
        return float(r["output"]["stck_prpr"])

    def _order(self, tr_id: str, code: str, qty: int, price: Optional[int]) -> dict:
        body = {"CANO": self.cano, "ACNT_PRDT_CD": self.prdt, "PDNO": code,
                "ORD_DVSN": "00" if price else "01",               # 00 지정가, 01 시장가
                "ORD_QTY": str(int(qty)), "ORD_UNPR": str(int(price or 0))}
        r = self.requests.post(f"{self.base}/uapi/domestic-stock/v1/trading/order-cash",
                               headers=self._headers(tr_id), data=json.dumps(body), timeout=10).json()
        ok = r.get("rt_cd") == "0"
        return {"ok": ok, "msg": r.get("msg1", ""), "order_no": (r.get("output") or {}).get("ODNO"),
                "price": price, "qty": qty}

    def buy(self, code, qty, price):
        return self._order(self._tr("tr_buy"), code, qty, price)

    def sell(self, code, qty, price):
        return self._order(self._tr("tr_sell"), code, qty, price)

    def balance(self):
        params = {"CANO": self.cano, "ACNT_PRDT_CD": self.prdt, "AFHR_FLPR_YN": "N", "OFL_YN": "",
                  "INQR_DVSN": "02", "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N",
                  "PRCS_DVSN": "00", "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""}
        r = self.requests.get(f"{self.base}/uapi/domestic-stock/v1/trading/inquire-balance",
                              headers=self._headers(self._tr("tr_balance")), params=params, timeout=10).json()
        hold = {x["pdno"]: {"qty": int(x["hldg_qty"]), "avg": float(x["pchs_avg_pric"]), "price": float(x["prpr"])}
                for x in r.get("output1", []) if int(x.get("hldg_qty", 0)) > 0}
        o2 = (r.get("output2") or [{}])[0]
        return {"cash": float(o2.get("dnca_tot_amt", 0)), "holdings": hold, "total": float(o2.get("tot_evlu_amt", 0))}


# --------------------------------------------------------------------------- #
# 상태 / 기록
# --------------------------------------------------------------------------- #
class State:
    def __init__(self, d: str):
        self.dir = d
        os.makedirs(d, exist_ok=True)
        self.pos_path = os.path.join(d, "positions.json")
        self.positions: Dict[str, dict] = json.load(open(self.pos_path)) if os.path.exists(self.pos_path) else {}

    def save(self):
        with open(self.pos_path, "w") as f:
            json.dump(self.positions, f, ensure_ascii=False, indent=1)

    def stopped(self) -> bool:
        return os.path.exists(os.path.join(self.dir, "STOP"))

    def stop(self, reason: str):
        with open(os.path.join(self.dir, "STOP"), "w") as f:
            f.write(f"{datetime.now().isoformat()} {reason}\n")

    LOG_COLS = ["time", "action", "code", "qty", "price", "ok", "reason", "pnl_pct", "order_no", "msg"]

    def log(self, **row):
        p = os.path.join(self.dir, "orders.csv")
        rec = {"time": datetime.now().isoformat(timespec="seconds"), **row}
        pd.DataFrame([{k: rec.get(k, "") for k in self.LOG_COLS}]).to_csv(
            p, mode="a", header=not os.path.exists(p), index=False, encoding="utf-8-sig")

    def plan_path(self, d: date) -> str:
        return os.path.join(self.dir, f"plan_{d.strftime('%Y%m%d')}.json")


def in_window(win: List[str], now: datetime) -> bool:
    s, e = (datetime.strptime(x, "%H:%M").time() for x in win)
    return s <= now.time() <= e


def seoul_now() -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Seoul")).replace(tzinfo=None)
    except Exception:
        return datetime.now()


def trading_days_between(a: str, b: date) -> int:
    return int(np.busday_count(pd.Timestamp(a).date(), b))


def make_broker(cfg: dict, state: State, args) -> Broker:
    mode = cfg["mode"]
    if mode == "paper":
        prices = dict(x.split("=") for x in (args.price or []))
        return PaperBroker(state.dir, cfg["paper_cash"], args.csv_dir, {k: float(v) for k, v in prices.items()})
    if mode == "kis_mock":
        return KisBroker(cfg, live=False, state_dir=state.dir)
    if mode == "kis_live":
        if not cfg.get("allow_live") or os.environ.get("AUTOTRADE_LIVE_CONFIRM") != "YES":
            raise SystemExit("실전 모드는 config의 allow_live=true 와 환경변수 AUTOTRADE_LIVE_CONFIRM=YES 가 모두 필요합니다.")
        return KisBroker(cfg, live=True, state_dir=state.dir)
    raise SystemExit(f"알 수 없는 mode: {mode}")


# --------------------------------------------------------------------------- #
# 명령
# --------------------------------------------------------------------------- #
def cmd_plan(cfg, state, args):
    from breakout_finder import KR_UNIVERSE, US_UNIVERSE, load_from_csv_dir, load_from_yfinance
    from daily_top3_test import build_context, event_table, market_index, today_picks
    from indicators import TestConfig, compute
    from flows import load_flows
    names = dict(KR_UNIVERSE if cfg["market"] == "kr" else US_UNIVERSE)
    data = load_from_csv_dir(args.csv_dir, None) if args.csv_dir else load_from_yfinance(list(names), "5y")
    if not data:
        raise SystemExit("데이터를 불러오지 못했습니다.")
    tc = TestConfig(days=cfg["days"], picks=cfg["picks"], min_prob=cfg["min_prob"],
                    take_profit=cfg["take_profit"], stop_atr=cfg["stop_atr"])
    flows = load_flows(os.path.join(HERE, "flows_cache"))
    mkt = market_index(data)
    frames = {t: compute(df, tc, market=mkt, flows=flows.get(t)) for t, df in data.items()}
    ctx = build_context(frames, data, tc)
    ev = event_table(ctx)
    picks = today_picks(ctx, ev, frames, tc, cfg["strategy"], names)
    signal_day = ctx["eligible"].index[-1]
    items = []
    if not picks.empty and bool(picks["market_ok"].iloc[0]):
        for _, p in picks.iterrows():
            f = frames[p["ticker"]].loc[signal_day]
            items.append({"code": p["ticker"].split(".")[0], "ticker": p["ticker"], "name": p["name"],
                          "close": float(f["Close"]), "prior_high": float(f["prior_high"]),
                          "atr": float(f["atr14"]), "score": float(p["score"]),
                          "ml_prob": None if pd.isna(p["ml_prob"]) else float(p["ml_prob"])})
    plan = {"signal_date": str(signal_day.date()), "created": datetime.now().isoformat(timespec="seconds"),
            "strategy": cfg["strategy"], "market_ok": bool(picks["market_ok"].iloc[0]) if not picks.empty else None,
            "items": items}
    target_day = np.busday_offset(signal_day.date(), 1, roll="forward")
    path = state.plan_path(pd.Timestamp(target_day).date())
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=1)
    print(f"매수 계획 {len(items)}종목 → {path}")
    for it in items:
        print(f"  {it['name']}({it['code']}) 종가 {it['close']:,.0f} 전고점 {it['prior_high']:,.0f} "
              f"ML확률 {it['ml_prob']}")
    if not items:
        print("  조건 충족 종목이 없거나 시장 필터가 꺼져 있어 내일은 매수하지 않습니다.")


def daily_loss_check(cfg, state, broker) -> bool:
    b = broker.balance()
    path = os.path.join(state.dir, "equity_open.json")
    today = str(seoul_now().date())
    rec = json.load(open(path)) if os.path.exists(path) else {}
    if rec.get("date") != today:
        rec = {"date": today, "equity": b["total"]}
        json.dump(rec, open(path, "w"))
    loss = (b["total"] / rec["equity"] - 1) * 100 if rec["equity"] else 0
    if loss <= -cfg["max_daily_loss_pct"]:
        state.stop(f"하루 손실 {loss:.2f}% 한도 초과")
        print(f"하루 손실 {loss:.2f}%로 한도를 넘어 신규 매수를 멈춥니다 (STOP 생성).")
        return False
    return True


def cmd_enter(cfg, state, broker, args):
    now = seoul_now()
    if not args.force and not in_window(cfg["entry_window"], now):
        return print(f"매수 허용 시간({cfg['entry_window']})이 아닙니다.")
    if state.stopped():
        return print("STOP 파일이 있어 신규 매수를 하지 않습니다.")
    if not daily_loss_check(cfg, state, broker):
        return
    path = state.plan_path(now.date() if not args.date else pd.Timestamp(args.date).date())
    if not os.path.exists(path):
        return print(f"오늘 계획 파일이 없습니다: {path}")
    plan = json.load(open(path, encoding="utf-8"))
    for it in plan["items"]:
        code = it["code"]
        if len(state.positions) >= cfg["max_positions"]:
            print("최대 보유 종목 수에 도달했습니다.")
            break
        if code in state.positions:
            continue
        px = broker.price(code)
        gap = (px / it["close"] - 1) * 100
        if px >= it["prior_high"]:
            state.log(action="skip", code=code, reason="이미 전고점 위", price=px)
            print(f"  {it['name']}: 현재가 {px:,.0f}가 전고점 위라 추격 매수 안 함")
            continue
        if gap > cfg["max_gap_pct"]:
            state.log(action="skip", code=code, reason=f"갭 {gap:.1f}%", price=px)
            print(f"  {it['name']}: 갭 상승 {gap:.1f}%로 건너뜀")
            continue
        budget = min(cfg["capital_per_trade"], cfg["max_order_krw"])
        limit = round_tick(px, up=True)
        qty = int(budget // limit)
        if qty <= 0:
            continue
        r = broker.buy(code, qty, limit)
        state.log(action="buy", code=code, qty=qty, price=limit, ok=r["ok"], msg=r.get("msg", ""),
                  order_no=r.get("order_no"))
        if r["ok"]:
            fill = r.get("price") or limit
            state.positions[code] = {
                "name": it["name"], "qty": qty, "entry": fill, "entry_date": str(now.date()),
                "target": round_tick(it["prior_high"] * (1 + cfg["take_profit"]), up=False),
                "stop": round_tick(fill - cfg["stop_atr"] * it["atr"], up=False),
                "days": cfg["days"]}
            state.save()
            p = state.positions[code]
            print(f"  매수 {it['name']} {qty}주 @ {fill:,.0f}  익절 {p['target']:,} 손절 {p['stop']:,}")
        else:
            print(f"  매수 실패 {it['name']}: {r.get('msg')}")


def _exit(state, broker, code, reason, market: bool):
    p = state.positions[code]
    px = broker.price(code)
    r = broker.sell(code, p["qty"], None if market else round_tick(px, up=False))
    state.log(action="sell", code=code, qty=p["qty"], price=r.get("price") or px, ok=r["ok"],
              reason=reason, pnl_pct=round(((r.get("price") or px) / p["entry"] - 1) * 100, 2),
              order_no=r.get("order_no"), msg=r.get("msg", ""))
    if r["ok"]:
        print(f"  매도 {p['name']} {p['qty']}주 @ {r.get('price') or px:,.0f} ({reason}, "
              f"{((r.get('price') or px) / p['entry'] - 1) * 100:+.2f}%)")
        del state.positions[code]
        state.save()


def reconcile(state, broker) -> None:
    """장부와 증권사 잔고 비교. 주문 접수 ≠ 체결이므로 미체결·부분체결을 알려준다."""
    held = broker.balance()["holdings"]
    for code, p in state.positions.items():
        q = held.get(code, {}).get("qty", 0)
        if q != p["qty"]:
            print(f"  [확인 필요] {p['name']}({code}) 장부 {p['qty']}주, 증권사 잔고 {q}주. 미체결 또는 부분체결일 수 있습니다.")
            state.log(action="mismatch", code=code, qty=q, reason=f"장부 {p['qty']}주")


def cmd_monitor(cfg, state, broker, args):
    if not args.force and not in_window(cfg["monitor_window"], seoul_now()):
        return print("감시 시간대가 아닙니다.")
    daily_loss_check(cfg, state, broker)
    reconcile(state, broker)
    for code in list(state.positions):
        p, px = state.positions[code], broker.price(code)
        if px >= p["target"]:
            _exit(state, broker, code, "익절", market=False)
        elif px <= p["stop"]:
            _exit(state, broker, code, "손절", market=True)


def cmd_close(cfg, state, broker, args):
    now = seoul_now()
    if not args.force and not in_window(cfg["close_window"], now):
        return print(f"종가 청산 시간({cfg['close_window']})이 아닙니다.")
    for code in list(state.positions):
        p = state.positions[code]
        held = trading_days_between(p["entry_date"], now.date()) + 1
        if held >= p["days"]:
            _exit(state, broker, code, f"{held}일 보유 만료", market=True)


def cmd_status(cfg, state, broker, args):
    reconcile(state, broker)
    b = broker.balance()
    print(f"모드 {cfg['mode']} | 예수금 {b['cash']:,.0f} | 총평가 {b['total']:,.0f} | STOP {'있음' if state.stopped() else '없음'}")
    for code, p in state.positions.items():
        px = broker.price(code)
        print(f"  {p['name']}({code}) {p['qty']}주 매수가 {p['entry']:,.0f} 현재 {px:,.0f} "
              f"({(px / p['entry'] - 1) * 100:+.2f}%) 익절 {p['target']:,} 손절 {p['stop']:,} 진입 {p['entry_date']}")
    op = os.path.join(state.dir, "orders.csv")
    if os.path.exists(op):
        o = pd.read_csv(op)
        s = o[(o["action"] == "sell") & (o["ok"] == True)]  # noqa: E712
        if len(s):
            print(f"  누적 청산 {len(s)}건, 승률 {(s['pnl_pct'] > 0).mean()*100:.0f}%, 평균 {s['pnl_pct'].mean():+.2f}%")


def main():
    ap = argparse.ArgumentParser(description="자동매매 (모의 → KIS 모의투자 → 실전)")
    ap.add_argument("command", choices=["plan", "enter", "monitor", "close", "status"])
    ap.add_argument("--config", default=os.path.join(HERE, "autotrade_config.json"))
    ap.add_argument("--csv-dir", help="오프라인 일봉 CSV (plan·paper 가격용)")
    ap.add_argument("--price", nargs="*", help="paper 모드 테스트용 현재가 지정 (예 005930=70000)")
    ap.add_argument("--date", help="enter 에서 쓸 계획 날짜 (YYYY-MM-DD, 테스트용)")
    ap.add_argument("--force", action="store_true", help="시간대 제한 무시 (테스트용)")
    a = ap.parse_args()
    cfg = load_config(a.config)
    state = State(os.path.join(HERE, cfg["state_dir"]) if not os.path.isabs(cfg["state_dir"]) else cfg["state_dir"])
    if a.command == "plan":
        return cmd_plan(cfg, state, a)
    broker = make_broker(cfg, state, a)
    {"enter": cmd_enter, "monitor": cmd_monitor, "close": cmd_close, "status": cmd_status}[a.command](
        cfg, state, broker, a)


if __name__ == "__main__":
    main()
