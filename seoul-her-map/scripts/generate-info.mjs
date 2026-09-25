/** 실용 정보 노트: node scripts/generate-info.mjs --slug gimpo-arrival [--no-render]
 *  카드 = 커버 1 + 스텝 카드 1 + 아웃트로 1. 본문은 스텝을 그대로 풀어 쓴다. */
import fs from "node:fs";
import path from "node:path";
import { readCsv, render, esc, today, p } from "./lib/util.mjs";
import { BRAND, makeChecklist } from "./lib/copy.mjs";
import { renderDir } from "./render.mjs";

const args = Object.fromEntries(process.argv.slice(2).map((a, i, arr) =>
  a.startsWith("--") ? [a.slice(2), arr[i + 1] && !arr[i + 1].startsWith("--") ? arr[i + 1] : true] : []).filter(x => x.length));
const info = readCsv(p("data/info.csv")).find(x => x.slug === args.slug);
if (!info) throw new Error(`unknown info slug: ${args.slug}. data/info.csv 참고`);
const keywords = readCsv(p("data/keywords.csv"));
const steps = info.steps_zh.split("｜").map(s => s.replace(/^\d+\.\s*/, "").trim()).filter(Boolean);

const title = `${info.title_zh}｜${info.hook_zh}`.slice(0, 30);
const tags = [...keywords.filter(k => k.tier === "big").slice(0, 2).map(k => k.keyword_zh), ...info.tags_zh.split(";")].slice(0, 8);
const body = [`姐妹们，${info.hook_zh}👇`, ...steps.map((s, i) => `${i + 1}️⃣ ${s}`), `📌 ${info.tip_zh}`, info.avoid_zh ? `⚠️ 避雷：${info.avoid_zh}` : "",
  "收藏起来，到了直接照着做。还有什么想知道的评论区问我。", tags.map(t => "#" + t).join(" ")].filter(Boolean).join("\n\n");

const noteDir = p("out", `${today()}-info-${info.slug}`);
const cardsDir = path.join(noteDir, "cards");
fs.mkdirSync(cardsDir, { recursive: true });
fs.copyFileSync(p("templates/theme.css"), path.join(cardsDir, "theme.css"));
const T = n => fs.readFileSync(p("templates", n), "utf8");
const common = { brand_zh: BRAND.zh, brand_en: BRAND.en, ai_label: BRAND.aiLabel, area_zh: "首尔", area_en: "Seoul", count: steps.length,
  date: today(), date_short: today().slice(0, 7).replace("-", ".") };

fs.writeFileSync(path.join(cardsDir, "01-cover.html"), render(T("cover.html"), { ...common, signature: esc(info.title_zh), area_zh: "", category_zh: "",
  unit_zh: "步", hook_zh: esc(info.hook_zh), cover_image: "", cover_image_2: "", cover_image_3: "", cover_image_ph: "placeholder", cover_image_2_ph: "placeholder", cover_image_3_ph: "placeholder", sticker_zh: "实用帖", avg_price_zh: "0 · 免费", vibe_line: "收藏率最高的一类，落地前看一遍" }));
fs.writeFileSync(path.join(cardsDir, "02-steps.html"), render(T("info.html"), { ...common, title_zh: esc(info.title_zh), hook_zh: esc(info.hook_zh),
  step_items: steps.map((s, i) => `<li><div class="n">${i + 1}</div><div>${esc(s)}</div></li>`).join(""), tip_zh: esc(info.tip_zh), avoid_zh: esc(info.avoid_zh), avoid_display: info.avoid_zh ? "block" : "none" }));
fs.writeFileSync(path.join(cardsDir, "03-outro.html"), render(T("outro.html"), { ...common, outro_zh: "还想看哪一类实用帖？退税、交通、预约，评论区点名。",
  tag_chips: tags.map(t => `<span class="chip" style="border-color:rgba(245,240,232,.4);color:var(--cream)">#${esc(t)}</span>`).join("") }));

fs.writeFileSync(path.join(noteDir, "title.txt"), title + "\n");
fs.writeFileSync(path.join(noteDir, "body.txt"), body + "\n");
fs.writeFileSync(path.join(noteDir, "tags.txt"), tags.map(t => "#" + t).join(" ") + "\n");
fs.writeFileSync(path.join(noteDir, "checklist.md"), makeChecklist({ title }) + "\n");
console.log(`✔ ${path.relative(p(), noteDir)}\n  title: ${title}`);
if (!args["no-render"]) await renderDir(cardsDir);
