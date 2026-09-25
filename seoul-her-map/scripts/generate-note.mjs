/**
 * 사용법:
 *   node scripts/generate-note.mjs --area magok --category cafe            # 해당 동네·카테고리 전체
 *   node scripts/generate-note.mjs --area magok --slugs a,b,c              # 지정한 장소만, 이 순서로
 *   node scripts/generate-note.mjs --area magok --category spot --no-render
 * 출력: out/<date>-<area>-<category>/{title.txt, body.txt, tags.txt, narration.txt, checklist.md, cards/*.html|png}
 */
import fs from "node:fs";
import path from "node:path";
import { readCsv, render, esc, today, p } from "./lib/util.mjs";
import { BRAND, makeTitle, makeBody, makeTags, makeNarration, makeChecklist } from "./lib/copy.mjs";
import { renderDir } from "./render.mjs";

const args = Object.fromEntries(process.argv.slice(2).map((a, i, arr) =>
  a.startsWith("--") ? [a.slice(2), arr[i + 1] && !arr[i + 1].startsWith("--") ? arr[i + 1] : true] : []).filter(x => x.length));

const areas = readCsv(p("data/areas.csv"));
const cats = readCsv(p("data/categories.csv"));
const keywords = readCsv(p("data/keywords.csv"));
const all = readCsv(p("data/places.csv"));

const area = areas.find(a => a.area === (args.area || "magok"));
if (!area) throw new Error(`unknown area: ${args.area}`);

let places = all.filter(x => x.area === area.area && !x.slug.startsWith("sample-"));
if (args.slugs) {
  const order = String(args.slugs).split(",");
  places = order.map(s => all.find(x => x.slug === s)).filter(Boolean);
} else if (args.category) {
  places = places.filter(x => x.category === args.category);
}
places.sort((a, b) => (+a.order || 99) - (+b.order || 99));
if (!places.length) throw new Error("no places matched. data/places.csv 를 확인하세요 (sample- 접두어 행은 제외됨)");

const catKey = args.category || places[0].category;
const category = cats.find(c => c.category === catKey) || cats[0];
const signature = args.signature || keywords.find(k => k.tier === "signature").keyword_zh;
const count = places.length;

const title = makeTitle({ signature, area, category, count });
const body = makeBody({ area, category, places, keywords });
const tags = makeTags({ area, category, keywords });
const narration = makeNarration({ area, places });

const noteDir = p("out", `${today()}-${area.area}-${catKey}`);
const cardsDir = path.join(noteDir, "cards");
fs.mkdirSync(cardsDir, { recursive: true });
fs.copyFileSync(p("templates/theme.css"), path.join(cardsDir, "theme.css"));

const T = n => fs.readFileSync(p("templates", n), "utf8");
const img = f => (f && fs.existsSync(p("images", f))) ? path.relative(cardsDir, p("images", f)) : "";
const mascot = fs.existsSync(p("images/mascot.png")) ? path.relative(cardsDir, p("images/mascot.png")) : "";
const common = { brand_zh: BRAND.zh, brand_en: BRAND.en, ai_label: BRAND.aiLabel, area_zh: area.area_zh, area_en: area.area_en,
  station_zh: area.station_zh, count, date: today() };

const write = (i, name, html) => fs.writeFileSync(path.join(cardsDir, `${String(i).padStart(2, "0")}-${name}.html`), html);

write(1, "cover", render(T("cover.html"), { ...common, signature: esc(signature), category_zh: category.category_zh,
  unit_zh: category.unit_zh, hook_zh: esc(area.hook_zh.replace("｜", " · ")), cover_image: img(places[0].image) }));

places.forEach((pl, i) => write(i + 2, pl.slug, render(T("place.html"), { ...common, ...Object.fromEntries(Object.entries(pl).map(([k, v]) => [k, esc(v)])),
  order: i + 1, category_zh: (cats.find(c => c.category === pl.category) || category).category_zh,
  walk_min: pl.walk_min_from_station || "?", station_short: area.station_zh.split("(")[0], image: img(pl.image) })));

const routeItems = places.map((pl, i) => `<li><div class="dot">${i + 1}</div><div><div class="nm">${esc(pl.name_zh)}</div><div class="mt">${esc(pl.one_liner_zh)}</div></div><div class="walk">${esc(pl.walk_min_from_station || "?")} MIN</div></li>`).join("");
const totalWalk = places.reduce((s, x) => s + (+x.walk_min_from_station || 0), 0);
write(places.length + 2, "route", render(T("route.html"), { ...common, route_items: routeItems, total_walk: totalWalk }));

write(places.length + 3, "outro", render(T("outro.html"), { ...common,
  outro_zh: esc(`下一期写${areas.filter(a => a.area !== area.area).slice(0, 2).map(a => a.area_zh).join("和")}，想先看哪个？`),
  tag_chips: tags.map(t => `<span class="chip" style="border-color:rgba(245,240,232,.4);color:var(--cream)">#${esc(t)}</span>`).join("") }));

fs.writeFileSync(path.join(noteDir, "title.txt"), title + "\n");
fs.writeFileSync(path.join(noteDir, "body.txt"), body + "\n");
fs.writeFileSync(path.join(noteDir, "tags.txt"), tags.map(t => "#" + t).join(" ") + "\n");
fs.writeFileSync(path.join(noteDir, "narration.txt"), narration + "\n");
fs.writeFileSync(path.join(noteDir, "checklist.md"), makeChecklist({ title }) + "\n");

console.log(`✔ ${path.relative(p(), noteDir)}\n  title: ${title}\n  cards: ${places.length + 3}`);
if (args["no-render"]) { console.log("  (렌더 생략: cards/*.html 를 브라우저로 열어 확인)"); }
else { console.log("  rendering PNG…"); await renderDir(cardsDir); }
