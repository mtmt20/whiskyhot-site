/** 노트 폴더의 카드 PNG 전체를 한 장(3열)으로 합쳐 검토용 contact.png 생성. 사용: node scripts/contact-sheet.mjs out/<note> */
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { execSync } from "node:child_process";
const dir = process.argv[2]; if (!dir) { console.error("usage: node scripts/contact-sheet.mjs out/<note>"); process.exit(1); }
const cards = fs.readdirSync(path.join(dir, "cards")).filter(f => f.endsWith(".png")).sort();
const g = execSync("npm root -g").toString().trim();
const { chromium } = createRequire(import.meta.url)(path.join(g, "playwright"));
const cols = 3, w = 414, h = 552, gap = 16;
const rows = Math.ceil(cards.length / cols);
const html = `<body style="margin:0;background:#e6e0d4;padding:${gap}px;display:grid;grid-template-columns:repeat(${cols},${w}px);gap:${gap}px">
${cards.map(c => `<img src="data:image/png;base64,${fs.readFileSync(path.join(dir, "cards", c)).toString("base64")}" style="width:${w}px;height:${h}px;border-radius:6px;box-shadow:0 4px 14px rgba(0,0,0,.15)">`).join("")}</body>`;
const b = await chromium.launch(); const p = await b.newPage({ viewport: { width: cols * w + gap * (cols + 1), height: rows * h + gap * (rows + 1) } });
await p.setContent(html); await p.waitForLoadState("networkidle");
await p.screenshot({ path: path.join(dir, "contact.png") }); await b.close();
console.log("✔", path.join(dir, "contact.png"), `(${cards.length} cards)`);
