# 首尔她地图 · SEOUL HER MAP

샤오홍슈(小红书)용 "한국 여자들이 진짜 가는 서울 놀거리" 카루셀 노트 생성기.
CSV에 장소를 넣으면 중국어 제목·본문·해시태그·나레이션 대본과 3:4 카드 이미지가 한 폴더로 나온다.
얼굴 노출 없이 운영하는 걸 전제로 한다(이미지 노트 + 캐릭터 워터마크 + TTS).

## 빠른 시작

```bash
npm install            # playwright (Chromium 은 npx playwright install chromium)
npm run note -- --area magok --category spot
# → out/2026-09-25-magok-spot/{title.txt, body.txt, tags.txt, narration.txt, checklist.md, cards/*.png}
```

옵션
- `--area magok|seongsu|dosan|yeonnam|hannam`  동네 (data/areas.csv)
- `--category cafe|restaurant|bakery|beauty|shop|spot`  카테고리 (data/categories.csv)
- `--slugs a,b,c`  장소를 직접 골라 그 순서로
- `--signature 本地闺蜜私藏`  제목 앞머리 시그니처 바꾸기
- `--no-render`  PNG 생략, cards/*.html 만 생성 (브라우저로 열어 미리보기)

영상 노트로 만들 때: `scripts/tts.sh out/<note>` 로 narration.mp3 를 만들고, 剪映(CapCut 중국판)에 cards/*.png 를 3초씩 올려 합친다.

## 데이터

| 파일 | 역할 |
|---|---|
| data/places.csv | 장소 한 줄 = 카드 한 장. `sample-` 접두어 행은 예시라서 생성에서 제외됨 |
| data/areas.csv | 동네별 중국어 이름, 훅 문장, 지하철역, 롱테일 태그 |
| data/keywords.csv | 큰 키워드 / 시그니처 / 롱테일 3층 |
| data/categories.csv | 카테고리 중국어 이름과 단위(家/处) |

places.csv 열 설명
- `one_liner_zh` 한 줄 훅(12자 내외). 카드 제목과 동선 카드에 들어감
- `detail_zh` 두세 문장. 구체적으로: 시그니처 메뉴, 자리, 빛, 몇 명이 가기 좋은지
- `tip_zh` 시간대·주문·웨이팅 회피 팁. 저장률을 올리는 칸
- `walk_min_from_station` 역에서 도보 분. 동선 카드 합계에 씀
- `order` 동선 순서
- `image` images/ 안의 파일명

## 카피 규칙 (scripts/lib/copy.mjs)

- 제목 = 시그니처 + 동네 + 카테고리 + 숫자 ｜ 훅. 예: `韩国女生都在去的麻谷咖啡厅5家｜金浦机场15分钟`
- 본문 = 훅 한 줄 → 장소별 블록(이름·훅·설명·가격/시간/도보·팁) → 마무리 CTA → 해시태그
- 해시태그 = 큰 키워드 3 + 시그니처 1 + 동네 롱테일 3 + 카테고리 1, 최대 8개
- 카드 = 커버 1 + 장소 N + 동선 1 + 아웃트로 1

## 디자인 규칙 (templates/theme.css)

색 3개 고정: 크림 `#f5f0e8`, 차콜 `#2b2a27`, 세이지 `#7a8f7b`. 제목은 명조(Noto Serif SC), 본문은 고딕, 영문 라벨은 Cormorant.
크기 1242×1656(3:4). 모든 카드 오른쪽 위에 `含AI生成内容` 라벨이 들어간다. 캐릭터·TTS를 쓰면 이 표시는 필수다.

## 운영 체크리스트

각 노트 폴더의 checklist.md 참고. 핵심: 제목 20자 내외, 태그 8개 이하, 위치는 지하철역으로, 발행은 中国时间 12:00 또는 21:00, 발행 후 1시간 안에 댓글 답글.

## 수익화 순서

1. 카페·식당 협찬 방문(한국 매장에 직접 제안, 인증 조건 없음)
2. 蒲公英 브랜드 협찬(팔로워 1,000 + 실명인증)
3. 제휴 링크(Olive Young Global, Klook, Trip.com, Creatrip) — 본문 외부 링크 불가, 프로필·私信 자동응답으로 유도
