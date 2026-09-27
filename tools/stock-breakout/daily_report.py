#!/usr/bin/env python3
"""
일일 추천 보고서. 장 마감 후 autotrade.py plan 다음에 실행한다 (16:20).

담는 내용
  1. 한 줄 요약과 시장 상태 (매매하는 날인지)
  2. 내일 모의매수 후보: 전고점, 익절·손절 예상가, ML 확률
  3. 모의매매 현황: 보유 종목 손익, 누적 승률·수익률
  4. 실험실 최신 순위 상위 3개 (lab_results/leaderboard.csv)
  5. 최근 3거래일 공시 신호: 내부자 동시 매수, 실적 증가, 흑자전환, 자사주 소각 (events.csv)
  6. 다가오는 일정: 증여세 평가 종료 등 (upcoming_events.csv)
  7. 경고: STOP 스위치, 장부·잔고 불일치

출력
  reports/daily_YYYYMMDD.md, reports/latest.html  (휴대폰 브라우저로 보기 좋게)
  텔레그램: 환경변수 TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 가 있으면 요약 전송

모든 내용은 모의매매·과거 검증 기반 참고 정보이며 투자 권유가 아니다.
"""
from __future__ import annotations

import argparse
import glob
import html
import json
import os
import sys
from datetime import date, datetime, timedelta
from typing import List, Optional

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

GOOD_EVENTS = {"insider_cluster": "내부자 여러 명 매수", "insider_buy": "내부자 매수", "earnings_up": "영업이익 급증",
               "turnaround": "흑자전환", "cancel": "자사주 소각", "buyback": "자사주 매입", "valueup": "밸류업 계획",
               "tender": "공개매수"}
BAD_EVENTS = {"cb": "전환사채·BW 발행", "rights": "유상증자"}


EXTRA_NAMES = {"003540": "대신증권", "001800": "오리온홀딩스", "214420": "토니모리", "026960": "동서",
               "294630": "서남", "271560": "오리온"}


class _Names(dict):
    """코드 → 종목명. 기본 유니버스·자주 보는 종목 → pykrx 조회 → 코드 순서로 찾는다."""
    def get(self, code, default=None):
        code = str(code).zfill(6)
        if code in self:
            return self[code]
        try:
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                from pykrx import stock
                nm = stock.get_market_ticker_name(code)
            if isinstance(nm, str) and nm:
                self[code] = nm
                return nm
        except Exception:
            pass
        return default if default is not None else code


_NAMES: Optional[_Names] = None


def names_map() -> dict:
    global _NAMES
    if _NAMES is None:
        from breakout_finder import KR_UNIVERSE
        _NAMES = _Names({k.split(".")[0]: v for k, v in KR_UNIVERSE.items()})
        _NAMES.update(EXTRA_NAMES)
    return _NAMES


def load_plan(state_dir: str, day: date) -> Optional[dict]:
    """day 이후 가장 가까운 계획 (보통 다음 거래일 것)."""
    files = sorted(glob.glob(os.path.join(state_dir, "plan_*.json")))
    for f in reversed(files):
        return json.load(open(f, encoding="utf-8")) | {"_file": os.path.basename(f)}
    return None


def section_plan(plan: Optional[dict], cfg: dict) -> List[str]:
    L = ["## 내일 모의매수 후보"]
    if not plan:
        return L + ["- 계획 파일이 없습니다. `autotrade.py plan`이 먼저 실행돼야 합니다.", ""]
    day = plan["_file"].replace("plan_", "").replace(".json", "")
    if plan.get("market_ok") is False:
        return L + [f"- {day[:4]}-{day[4:6]}-{day[6:]}: 시장 필터가 꺼져 있어 **매수하지 않는 날**입니다.", ""]
    if not plan["items"]:
        return L + ["- 조건을 통과한 종목이 없어 쉬어갑니다.", ""]
    L += [f"전략 `{plan['strategy']}` · 신호일 {plan['signal_date']} · 09:30~10:00 지정가, 전고점 위로 갭 상승하면 건너뜀", "",
          "| 종목 | 확률 | 전고점 | 익절 | 손절 |", "|---|---|---|---|---|"]
    for it in plan["items"]:
        tgt = it["prior_high"] * (1 + cfg["take_profit"])
        stop = it["close"] - cfg["stop_atr"] * it["atr"]
        prob = f"{it['ml_prob']*100:.0f}%" if it.get("ml_prob") is not None else "-"
        L.append(f"| {it['name']} | {prob} | {it['prior_high']:,.0f} | {tgt:,.0f} | {stop:,.0f} |")
    return L + [""]


def section_paper(state_dir: str, cfg: dict) -> List[str]:
    L = ["## 모의매매 현황"]
    acct_p = os.path.join(state_dir, "paper_account.json")
    pos_p = os.path.join(state_dir, "positions.json")
    positions = json.load(open(pos_p, encoding="utf-8")) if os.path.exists(pos_p) else {}
    if os.path.exists(acct_p):
        acct = json.load(open(acct_p))
        hold_val = sum(h["qty"] * h["avg"] for h in acct["holdings"].values())
        total = acct["cash"] + hold_val
        L.append(f"- 평가금액(매수가 기준) {total:,.0f}원, 시작 대비 {(total / cfg['paper_cash'] - 1) * 100:+.2f}%")
    if positions:
        L += ["", "| 보유 | 매수가 | 익절 | 손절 | 진입 |", "|---|---|---|---|---|"]
        for code, p in positions.items():
            L.append(f"| {p['name']} | {p['entry']:,.0f} | {p['target']:,} | {p['stop']:,} | {p['entry_date'][5:]} |")
    else:
        L.append("- 보유 종목 없음")
    op = os.path.join(state_dir, "orders.csv")
    if os.path.exists(op):
        o = pd.read_csv(op)
        s = o[(o["action"] == "sell") & (o["ok"].astype(str) == "True")]
        if len(s):
            pnl = pd.to_numeric(s["pnl_pct"], errors="coerce")
            last = s.tail(3)
            L.append(f"- 누적 청산 {len(s)}건 · 승률 {(pnl > 0).mean()*100:.0f}% · 평균 {pnl.mean():+.2f}% · "
                     f"익절 {(s['reason'] == '익절').sum()} / 손절 {(s['reason'] == '손절').sum()}")
            nm = names_map()
            L.append("- 최근 청산: " + ", ".join(
                f"{nm.get(str(r['code']).zfill(6), r['code'])} {float(r['pnl_pct']):+.1f}% ({r['reason']})"
                for _, r in last.iterrows()))
            mis = o[o["action"] == "mismatch"]
            if len(mis) and str(mis["time"].iloc[-1])[:10] == str(date.today()):
                L.append("- ⚠ 오늘 장부와 증권사 잔고가 달랐던 종목이 있습니다. `autotrade.py status`로 확인하세요.")
    return L + [""]


def section_lab(path: str) -> List[str]:
    L = ["## 실험실 순위 (검증 구간, 시장 대비 초과수익 기준)"]
    if not os.path.exists(path):
        return L + ["- 아직 실험실 결과가 없습니다. `lab.py`를 한 번 실행하세요.", ""]
    lb = pd.read_csv(path).head(3)
    upd = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d")
    L += [f"기준일 {upd}", "", "| 전략 | 초과수익 | 승률 | 판정 |", "|---|---|---|---|"]
    short = {"채택 후보": "채택", "유망, 추가 검증": "유망", "표본 부족": "표본↓", "기각": "기각"}
    for _, r in lb.iterrows():
        exc = r.get("검증_초과%")
        exc_s = f"{exc:+.2f}%" if pd.notna(exc) else "-"
        win_s = f"{r['검증_승률']:.0f}%" if pd.notna(r.get("검증_승률")) else "-"
        L.append(f"| {r['전략']} | {exc_s} | {win_s} | {short.get(r['판정'], r['판정'])} |")
    return L + [""]


def section_events(ev_path: str, up_path: str, today: date) -> List[str]:
    L = ["## 공시 신호 (최근 3거래일)"]
    nm = names_map()
    if os.path.exists(ev_path):
        ev = pd.read_csv(ev_path, dtype={"code": str}, parse_dates=["date"])
        since = pd.Timestamp(today) - pd.tseries.offsets.BDay(3)
        rec = ev[ev["date"] >= since]
        good = rec[rec["type"].isin(GOOD_EVENTS)]
        bad = rec[rec["type"].isin(BAD_EVENTS)]
        if len(good) or len(bad):
            for _, r in good.sort_values("date").iterrows():
                L.append(f"- ▲ {r['date'].date()} {nm.get(r['code'], r['code'])}: {GOOD_EVENTS[r['type']]} · {r['detail']}")
            for _, r in bad.sort_values("date").iterrows():
                L.append(f"- ▼ {r['date'].date()} {nm.get(r['code'], r['code'])}: {BAD_EVENTS[r['type']]} (피할 신호)")
        else:
            L.append("- 새 신호 없음")
        age = (datetime.now() - datetime.fromtimestamp(os.path.getmtime(ev_path))).days
        if age >= 2:
            L.append(f"- 이벤트 파일이 {age}일 전 것입니다. `events_from_dart.py`를 다시 돌리면 최신화됩니다.")
    else:
        L.append("- 이벤트 파일 없음 (`events_from_dart.py` 실행 필요, DART 키 필요)")
    L.append("")
    if os.path.exists(up_path):
        up = pd.read_csv(up_path, dtype={"code": str}, parse_dates=["date"])
        up = up[(up["date"] >= pd.Timestamp(today)) & (up["date"] <= pd.Timestamp(today) + timedelta(days=60))]
        if len(up):
            L.append("## 다가오는 일정 (60일)")
            label = {"gift_window_end": "증여세 평가기간 종료 → 이후 오너가 주가를 누를 이유가 약해짐"}
            for _, r in up.sort_values("date").iterrows():
                L.append(f"- {r['date'].date()} {nm.get(r['code'], r['code'])}: {label.get(r['type'], r['type'])}")
            L.append("")
    return L


def build(cfg: dict, state_dir: str, today: date, lab_path: str, ev_path: str, up_path: str) -> str:
    plan = load_plan(state_dir, today)
    stop = os.path.exists(os.path.join(state_dir, "STOP"))
    n = len(plan["items"]) if plan else 0
    if stop:
        head = "⛔ STOP 스위치가 켜져 있어 신규 모의매수를 멈춘 상태입니다."
    elif not plan:
        head = "계획이 아직 없습니다."
    elif plan.get("market_ok") is False:
        head = "시장 필터 OFF: 내일은 쉬는 날입니다."
    else:
        head = f"내일 모의매수 후보 {n}종목" + (": " + ", ".join(i["name"] for i in plan["items"]) if n else "")
    L = [f"# 일일 보고서 {today}", "", f"**{head}**", "",
         f"모드 `{cfg['mode']}` · 1종목 {cfg['capital_per_trade']:,}원 · 최대 {cfg['max_positions']}종목 · "
         f"보유 {cfg['days']}일 · 익절 전고점+{cfg['take_profit']*100:.0f}% · 손절 ATR×{cfg['stop_atr']}", ""]
    L += section_plan(plan, cfg)
    L += section_paper(state_dir, cfg)
    L += section_events(ev_path, up_path, today)
    L += section_lab(lab_path)
    L += ["---", "모의매매와 과거 검증에 기반한 참고 정보이며 투자 권유가 아닙니다."]
    return "\n".join(L)


def md_to_html(md: str, title: str) -> str:
    out, in_table = [], False
    for line in md.splitlines():
        esc = html.escape(line)
        esc = esc.replace("**", "\x00")
        while "\x00" in esc:
            esc = esc.replace("\x00", "<b>", 1).replace("\x00", "</b>", 1)
        esc = esc.replace("`", "")
        if line.startswith("|"):
            cells = [c.strip() for c in esc.strip("|").split("|")]
            if set("".join(cells)) <= set("-: "):
                continue
            if not in_table:
                out.append("<div class=t><table>")
                in_table = True
                out.append("<tr>" + "".join(f"<th>{c}</th>" for c in cells) + "</tr>")
            else:
                out.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
            continue
        if in_table:
            out.append("</table></div>")
            in_table = False
        if line.startswith("# "):
            out.append(f"<h1>{esc[2:]}</h1>")
        elif line.startswith("## "):
            out.append(f"<h2>{esc[3:]}</h2>")
        elif line.startswith("- "):
            out.append(f"<p class=li>{esc[2:]}</p>")
        elif line.strip() == "---":
            out.append("<hr>")
        elif line.strip():
            out.append(f"<p>{esc}</p>")
    if in_table:
        out.append("</table></div>")
    return f"""<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><style>
:root{{--bg:#fafaf8;--fg:#1d1d1b;--mut:#6b6b66;--line:#e2e1dc;--card:#fff;--acc:#2f5d8a}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151514;--fg:#ecebe6;--mut:#a09f98;--line:#33332f;--card:#1e1e1c;--acc:#8db4dd}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 -apple-system,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;padding:16px;max-width:760px;margin:auto}}
h1{{font-size:20px;margin:4px 0 12px}} h2{{font-size:16px;margin:22px 0 8px;color:var(--acc)}}
p{{margin:6px 0}} .li{{padding-left:12px;text-indent:-10px}} .li:before{{content:"· ";color:var(--mut)}}
.t{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;background:var(--card);font-size:14px}}
th,td{{border-bottom:1px solid var(--line);padding:6px 8px;text-align:right;white-space:nowrap}}
th:first-child,td:first-child{{text-align:left}} th{{color:var(--mut);font-weight:600}}
hr{{border:0;border-top:1px solid var(--line);margin:20px 0}}
@media (max-width:420px){{table{{font-size:13px}} th,td{{padding:5px 5px}} body{{padding:14px 12px}}}}
</style></head><body>{''.join(out)}</body></html>"""


def telegram(text: str) -> None:
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not (tok and chat):
        return
    import requests
    plain = text.replace("**", "").replace("`", "")
    for i in range(0, len(plain), 3900):
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                          data={"chat_id": chat, "text": plain[i:i + 3900]}, timeout=15)
        if not r.ok:
            print(f"텔레그램 전송 실패: {r.text[:120]}", file=sys.stderr)
            return
    print("텔레그램 전송 완료")


def main():
    from autotrade import load_config
    ap = argparse.ArgumentParser(description="일일 추천 보고서")
    ap.add_argument("--config", default=os.path.join(HERE, "autotrade_config.json"))
    ap.add_argument("--lab", default=os.path.join(HERE, "lab_results", "leaderboard.csv"))
    ap.add_argument("--events", default=os.path.join(HERE, "events.csv"))
    ap.add_argument("--upcoming", default=os.path.join(HERE, "upcoming_events.csv"))
    ap.add_argument("--out-dir", default=os.path.join(HERE, "reports"))
    ap.add_argument("--today", help="YYYY-MM-DD (테스트용)")
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args()
    cfg = load_config(a.config)
    state_dir = cfg["state_dir"] if os.path.isabs(cfg["state_dir"]) else os.path.join(HERE, cfg["state_dir"])
    today = datetime.strptime(a.today, "%Y-%m-%d").date() if a.today else date.today()
    md = build(cfg, state_dir, today, a.lab, a.events, a.upcoming)
    os.makedirs(a.out_dir, exist_ok=True)
    mdp = os.path.join(a.out_dir, f"daily_{today.strftime('%Y%m%d')}.md")
    with open(mdp, "w", encoding="utf-8") as f:
        f.write(md)
    with open(os.path.join(a.out_dir, "latest.html"), "w", encoding="utf-8") as f:
        f.write(md_to_html(md, f"일일 보고서 {today}"))
    print(md)
    print(f"\n저장: {mdp}, {os.path.join(a.out_dir, 'latest.html')}")
    if not a.no_telegram:
        telegram(md)


if __name__ == "__main__":
    main()
