# 전고점 돌파 확률 발굴기

과거 데이터로 "전고점 근처까지 올라온 뒤 실제로 돌파했는가"를 학습해,
지금 전고점 근처에 있는 종목의 돌파 확률을 추정하고 **60% 이상**인 종목만 골라냅니다.

## 설치 / 실행

```bash
pip install -r requirements.txt
python breakout_finder.py                 # 한국 대표 60여 종목, 60% 기준
python breakout_finder.py --market us     # 미국 대표 종목
python breakout_finder.py --tickers 005930.KS 000660.KS 247540.KQ
python breakout_finder.py --universe-file universe.txt --min-prob 0.65
python breakout_finder.py --csv-dir ./ohlcv   # 오프라인 CSV (Date,Open,High,Low,Close,Volume)
```

`universe.txt` 형식: 한 줄에 `티커,이름` (이름 생략 가능). 코스피 `.KS`, 코스닥 `.KQ`.

## 작동 방식

1. **전고점**: 최근 5일을 제외한 120거래일 최고가 (`--lookback`).
2. **접근 이벤트**: 종가가 전고점 아래 5% 이내 (`--near`).
3. **라벨**: 이벤트 후 20거래일 안에 고가가 전고점을 넘으면 돌파 성공 (`--horizon`, `--margin`).
4. **모델**: 전 종목 이벤트를 모아 로지스틱 회귀 학습 (numpy 구현, L2 정규화).
   피처는 전고점 이격, 거래량 비율, RSI, 이평선 위치·기울기, 변동성, 최근 60일 고점 터치 횟수, 고점 경과일, 20/60일 수익률 등 15개.
5. **검증**: 시간순 앞 70% 학습 → 뒤 30% 검증. AUC, 캘리브레이션, "예측 60% 이상 구간의 실제 돌파율"을 출력합니다.
6. **종합 확률**: 모델 확률 70% + 종목별 과거 돌파 기저율(표본 수로 수축) 30%.
7. 결과는 화면 출력 + `breakout_candidates.csv/json` 저장.

## 주요 옵션

| 옵션 | 기본 | 설명 |
|---|---|---|
| `--min-prob` | 0.60 | 발굴 기준 확률 |
| `--lookback` | 120 | 전고점 산정 거래일 |
| `--horizon` | 20 | 돌파 판정 거래일 |
| `--near` | 0.05 | 전고점 근접 기준 (5%) |
| `--margin` | 0.0 | 전고점 대비 추가 상승 요구 비율 |
| `--period` | 5y | yfinance 다운로드 기간 |

## 주의

과거 통계 기반 추정이며 투자 권유가 아닙니다. 출력되는 검증 AUC와
캘리브레이션(예측 확률 vs 실제 돌파율)이 나쁘면 확률을 신뢰하지 마세요.
