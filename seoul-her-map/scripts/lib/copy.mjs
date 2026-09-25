/** 샤오홍슈 노트 카피 생성. 규칙 기반이라 LLM 없이 돌아가고, 결과는 손으로 다듬는 것을 전제로 한다.
 *  톤: 중국 20대 후반 '闺蜜' 화법(姐妹, 真的, 直接冲) + 한국 20대 후반의 담백한 한 줄(짧고 감성, 과장 없음). 이모지는 블록당 1~2개만. */

export const BRAND = { zh: "首尔她地图", en: "SEOUL HER MAP", aiLabel: "含AI生成内容" };

/** 제목 공식: 시그니처 + 동네 + 카테고리 + 숫자 ｜ 훅  (20자 안팎이 검색·노출에 유리) */
export function makeTitle({ signature, area, category, count }) {
  return `${signature}${area.area_zh}${category.category_zh}${count}${category.unit_zh}｜${area.hook_zh.split("｜")[0]}`;
}

const VIBE_WORDS = { 5: "氛围感拉满", 4: "很有氛围", 3: "舒服", 2: "普通", 1: "一般" };

export function makeBody({ area, category, places, keywords }) {
  const intro = `姐妹们，${area.hook_zh.replace("｜", "，")}。\n这${places.length}家不是网红打卡店，是本地女生周末真的会去的。按动线排好了，直接照着走👇`;
  const items = places.map((pl, i) =>
    [`${i + 1}️⃣ ${pl.name_zh}｜${pl.name_ko}`,
     `「${pl.one_liner_zh}」`,
     pl.detail_zh,
     `${/^(人均|约|免费|\d)/.test(pl.price_zh) ? "" : "人均 "}${pl.price_zh}｜${pl.hours}｜${(pl.station_zh || area.station_zh).split("(")[0]} 步行${pl.walk_min_from_station}分钟`,
     pl.tip_zh ? `📌 ${pl.tip_zh}` : "",
     pl.avoid_zh ? `⚠️ 避雷：${pl.avoid_zh}` : ""].filter(Boolean).join("\n"));
  const outro = `最后一张是动线图，收藏起来到了直接用。\n想看${area.area_zh}的哪一类店，评论区告诉我，下一期就写。`;
  const tags = makeTags({ area, category, keywords }).map(t => `#${t}`).join(" ");
  return [intro, ...items, outro, tags].join("\n\n");
}

/** 해시태그: 큰 키워드 3 + 시그니처 1 + 동네 롱테일 3 + 카테고리 1 (최대 8개) */
export function makeTags({ area, category, keywords }) {
  const big = keywords.filter(k => k.tier === "big").slice(0, 3).map(k => k.keyword_zh);
  const sig = keywords.filter(k => k.tier === "signature").slice(0, 1).map(k => k.keyword_zh);
  const local = area.tags_zh.split(";").map(s => s.trim()).filter(Boolean).slice(0, 3);
  const cat = [`首尔${category.category_zh}`];
  return [...new Set([...big, ...sig, ...local, ...cat])].slice(0, 8);
}

/** 커버 아래 한 줄: 氛围 요약 */
export function makeVibeLine(places) {
  const avg = places.reduce((s, p) => s + (+p.vibe || 0), 0) / places.length;
  const tags = [...new Set(places.map(p => p.vibe_tag_zh).filter(Boolean))].slice(0, 3).join(" · ");
  return `${VIBE_WORDS[Math.round(avg)] || "舒服"}${tags ? " · " + tags : ""} · 不用排队的那种`;
}

/** 커버 스티커 문구: 동네 훅에서 두 번째 조각, 없으면 기본 */
export function makeSticker(area) {
  const seg = (area.hook_zh.split("｜")[1] || "").trim();
  return seg && seg.length <= 6 ? seg : "本地人认证";
}

/** 人均 대표값: 첫 유료 장소 가격, 없으면 免费 */
export function makeAvgPrice(places) {
  const paid = places.map(p => p.price_zh).find(v => v && !v.startsWith("免费"));
  return paid ? paid.replace(/^人均\s*/, "") : "0 · 免费";
}

/** TTS 나레이션 대본 (영상 노트용, 45~60초 분량). 문장 짧게, 숫자는 한자로 읽히게. */
export function makeNarration({ area, places }) {
  const lines = [`姐妹们，${area.hook_zh.replace("｜", "，")}。`];
  places.forEach((pl, i) => lines.push(`第${i + 1}站，${pl.name_zh}。${pl.one_liner_zh}。${pl.tip_zh || ""}`));
  lines.push("动线图在最后一张，记得收藏。我是首尔她地图，下次带你去别的街区。");
  return lines.join("\n");
}

export function makeChecklist({ title }) {
  return `# 发布清单 / 업로드 체크리스트
- [ ] 标题 20자 내외 확인: ${title}
- [ ] 封面 1장 = cards/01-cover.png, 나머지 순서대로
- [ ] 正文 body.txt 붙여넣기 후 이모지·줄바꿈 확인
- [ ] 话题 8개 이하, 본문 끝에만
- [ ] 地点 태그: 동네 지하철역으로 위치 등록
- [ ] AI 生成 표시 켜기 (캐릭터·나레이션 사용 시 필수)
- [ ] 发布 시간: 中国时间 12:00 또는 21:00
- [ ] 발행 후 1시간 안에 댓글 3개 이상 답글`;
}
