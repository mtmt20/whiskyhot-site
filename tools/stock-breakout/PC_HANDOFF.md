# PC 작업 지시서 (컴퓨터에서 실행되는 Claude 세션용)

이 문서를 읽은 Claude는 아래 순서대로 **사용자에게 묻지 않고** 진행한다. 사용자에게 물어야 하는 것은 API 키뿐이다.

## 충돌 방지 규칙 (반드시 지킬 것)

- 작업 폴더는 이 레포(`whiskyhot-site`)의 `tools/stock-breakout` 안으로 한정한다.
- 이 컴퓨터의 다른 레포(`whiskyhot` 등), 다른 Claude 세션, Docker 컨테이너, 실행 중인 서버는 건드리지 않는다. 종료·재시작·git 조작 금지.
- 전역 `pip install` 금지. 모든 패키지는 `setup.ps1`이 만드는 `tools/stock-breakout/.venv` 안에만 설치한다.
- 작업 스케줄러 등록(`schedule.ps1`)은 사용자가 명시적으로 원할 때만 한다.
- `autotrade_config.json`의 `mode`는 `paper`로 둔다. `kis_mock`, `kis_live`로 바꾸지 않는다. 실제 주문을 내지 않는다.
- 결과 파일(lab_results, stories 등)은 커밋하지 않는다(.gitignore 처리됨). 코드 버그를 고쳤을 때만 이 브랜치에 커밋·푸시한다.

## 1. 설치와 점검

```powershell
cd tools/stock-breakout
powershell -ExecutionPolicy Bypass -File setup.ps1
```

- Python이 없다고 나오면 사용자에게 python.org에서 3.11 이상 설치(“Add python.exe to PATH” 체크)를 부탁하고 멈춘다.
- `doctor.py` 결과에서 **시세(야후)** 가 문제면 방화벽·프록시를 확인하고, 해결 안 되면 원인을 보고한다.
- DART·KRX가 `[선택]` 미설정이면 2단계는 하되 3단계 중 공시가 필요한 부분은 건너뛰고, 마지막 보고에 키 발급 방법을 적는다.
  - DART 키: opendart.fss.or.kr 회원가입 → 인증키 신청 (무료, 즉시)
  - 설정: `setx DART_API_KEY "키"` 후 새 PowerShell 창

## 2. 시세만으로 되는 분석 (키 불필요)

```powershell
.\run.ps1 lab.py --note "PC 실데이터 첫 실행"
.\run.ps1 daily_top3_test.py --strategy all --no-files 2>$null
```

- 첫 실행은 66종목 5년 시세를 받느라 몇 분 걸린다.
- 에러가 나면 원인을 고치고(코드 버그면 수정 후 커밋), 다시 실행한다.

## 3. 공시가 필요한 분석 (DART 키 있을 때)

```powershell
.\run.ps1 events_from_dart.py --years 5
.\run.ps1 lab.py --events events.csv --note "공시 이벤트 포함"
.\run.ps1 story.py 003540 001800 214420
.\run.ps1 intent.py --months 12 --with-accumulation
```

## 4. 모의매매 준비 (주문 없음)

```powershell
.\run.ps1 autotrade.py plan
.\run.ps1 autotrade.py status
```

## 5. 일일 보고서

```powershell
.\run.ps1 daily_report.py --no-telegram
```

`reports/latest.html`이 만들어졌는지 확인한다. 사용자가 매일 자동 실행을 원하면 `schedule.ps1`로 등록한다 (StockLab_ 작업 5개).
텔레그램으로 받고 싶어 하면 README의 텔레그램 절차를 안내한다.

## 6. 사용자에게 보고할 내용

짧은 한국어로, 숫자는 표로:

1. 설치·점검 결과 (정상/문제 항목)
2. 실험실 순위표 상위 5개: 전략, 판정, 검증 거래 수, 승률, 평균수익, **시장 대비 초과수익**, 보정 p값
3. `채택 후보`가 있으면 그 전략과 설정. 없으면 “아직 없음”과 가장 가까운 전략
4. 공시 이벤트를 돌렸다면 이벤트 종류별 건수와 이벤트 전략 성적
5. 대신증권·오리온홀딩스·토니모리 스토리 판정 (돌렸다면)
6. 일일 보고서 생성 여부와 파일 위치
7. 다음 단계 제안 (키 발급, 모의매매 스케줄 등록 여부, 텔레그램 등)

주의: 과거 성과는 미래를 보장하지 않는다는 점과, 검증 구간 성적 기준이라는 점을 보고에 한 줄 넣는다.
