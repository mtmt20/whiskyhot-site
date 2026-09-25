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
- `--area magok|seongsu|yeonnam|hongdae|dosan|hannam`  동네 (data/areas.csv)
- `--category cafe|restaurant|bakery|beauty|shop|spot|mix`  카테고리. `mix`는 동네 전체를 한 노트로(제목은 好去处)
- `--slugs a,b,c`  장소를 직접 골라 그 순서로
- `--signature 本地闺蜜私藏`  제목 앞머리 시그니처 바꾸기
- `--no-render`  PNG 생략, cards/*.html 만 생성 (브라우저로 열어 미리보기)

실용 정보 노트(교통·퇴세·예약): `node scripts/generate-info.mjs --slug gimpo-arrival` (data/info.csv 에 정의, 커버+스텝+아웃트로 3장).

영상 노트로 만들 때: `scripts/tts.sh out/<note>` 로 narration.mp3 를 만들고 `scripts/video.sh out/<note>` 로 note.mp4 를 합친다.
video.sh 는 시스템 ffmpeg(libx264·png)가 필요하다. 맥은 `brew install ffmpeg`. 이 저장소를 만든 클라우드 환경에는 그 ffmpeg 가 없어 mp4 합성만 미검증이며, 나머지는 실제 렌더로 확인했다. 剪映에 PNG 를 직접 올려도 된다.

## 데이터

현재 검증된 장소: 마곡 2, 성수 6, 연남 4, 홍대 4, 도산 3, 한남 2 (2026-09-25 웹 확인). 누데이크 성수 본점은 폐업했고 하우스 노웨어 5층 티하우스로 넣었다.

| 파일 | 역할 |
|---|---|
| data/places.csv | 장소 한 줄 = 카드 한 장. `sample-` 접두어 행은 예시라서 생성에서 제외됨 |
| data/areas.csv | 동네별 중국어 이름, 훅 문장, 지하철역, 롱테일 태그 |
| data/keywords.csv | 큰 키워드 / 시그니처 / 롱테일 3층 |
| data/categories.csv | 카테고리 중국어 이름과 단위(家/处) |
| data/info.csv | 실용 정보 노트(스텝형) |
| data/calendar.csv | 첫 12개 노트 발행 순서와 명령 |
| docs/STRATEGY.md · ACCOUNT.md · PITCH.md | 운영 전략, 프로필·合集·私信 문구, 협찬 제안서 |
| images/mascot.svg · mascot.png | 뒷모습 단발 캐릭터(얼굴 없음). 커버 오른쪽 아래와 프로필용 |

places.csv 열 설명
- `one_liner_zh` 한 줄 훅(12자 내외). 카드 제목과 동선 카드에 들어감
- `detail_zh` 두세 문장. 구체적으로: 시그니처 메뉴, 자리, 빛, 몇 명이 가기 좋은지
- `tip_zh` 시간대·주문·웨이팅 회피 팁. 저장률을 올리는 칸
- `avoid_zh` 避雷 한 줄. 비우면 카드에서 사라짐
- `vibe`,`photo`,`quiet` 1~5 점수. `vibe_tag_zh` 사진 위 스티커 문구
- `station_zh` 장소별 지하철역(비우면 동네 기본역). 서울숲처럼 다른 역이 가까울 때
- `walk_min_from_station` 역에서 도보 분. 동선 카드 합계에 씀
- `source_url`, `verified_on` 검증 출처와 날짜. 영업시간·주소는 이 출처 기준이며 발행 전 네이버지도로 한 번 더 확인
- `order` 동선 순서
- `image` images/ 안의 파일명

## 카피 규칙 (scripts/lib/copy.mjs)

- 제목 = 시그니처 + 동네 + 카테고리 + 숫자 ｜ 훅. 예: `韩国女生都在去的麻谷咖啡厅5家｜金浦机场15分钟`
- 본문 = 훅 한 줄 → 장소별 블록(이름·훅·설명·가격/시간/도보·팁) → 마무리 CTA → 해시태그
- 해시태그 = 큰 키워드 3 + 시그니처 1 + 동네 롱테일 3 + 카테고리 1, 최대 8개
- 카드 = 커버 1 + 장소 N + 동선 1 + 아웃트로 1

## 디자인 규칙 (templates/theme.css)

중국 20대 후반 취향과 한국 20대 후반 취향을 반씩 섞는다.

| 한국식(유지) | 중국식(추가) |
|---|---|
| 넓은 여백, 명조 제목, 작은 영문 라벨 | 굵은 훅 한 줄 + 마커 하이라이트(버터색) |
| 톤다운 팔레트: 크림·차콜·세이지 | 스티커 라벨(피치색, 살짝 기울임), 커버 사진 콜라주 3장 |
| 필름 그레인, 과장 없는 문장 | 氛围·出片·安静 점수, 人均 가격, 📌팁과 ⚠️避雷 스트립 |

크기 1242×1656(3:4). 이모지는 블록당 1~2개. 모든 카드 오른쪽 위에 `含AI生成内容` 라벨이 들어간다. 캐릭터·TTS를 쓰면 이 표시는 필수다.

places.csv 의 `vibe`/`photo`/`quiet`(1~5)와 `vibe_tag_zh`(松弛感·氛围感·极简 등), `avoid_zh`(避雷, 비우면 숨김), `image_2`/`image_3`(커버 콜라주)가 이 요소들을 채운다.

## 운영 체크리스트

각 노트 폴더의 checklist.md 참고. 핵심: 제목 20자 내외, 태그 8개 이하, 위치는 지하철역으로, 발행은 中国时间 12:00 또는 21:00, 발행 후 1시간 안에 댓글 답글.

## 수익화 순서

1. 카페·식당 협찬 방문(한국 매장에 직접 제안, 인증 조건 없음)
2. 蒲公英 브랜드 협찬(팔로워 1,000 + 실명인증)
3. 제휴 링크(Olive Young Global, Klook, Trip.com, Creatrip) — 본문 외부 링크 불가, 프로필·私信 자동응답으로 유도
