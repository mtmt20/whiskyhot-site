/** 레이아웃 테스트용 중립 이미지(세이지 그라데이션 + 격자) 생성. 실제 사진 대용. 
 *  사용: node scripts/make-test-image.mjs images/seoul-forest.jpg [images/...]  → 테스트 후 삭제할 것 */
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { execSync } from "node:child_process";
const files = process.argv.slice(2);
if (!files.length) { console.error("usage: node scripts/make-test-image.mjs <out.jpg> ..."); process.exit(1); }
const g = execSync("npm root -g").toString().trim();
const { chromium } = createRequire(import.meta.url)(path.join(g, "playwright"));
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } });
for (const f of files) {
  const label = path.basename(f, path.extname(f)).toUpperCase();
  await page.setContent(`<body style="margin:0"><div style="width:1600px;height:1200px;display:flex;align-items:center;justify-content:center;
    background:linear-gradient(135deg,#b8c7b6,#7a8f7b 60%,#5e705f);background-size:cover;font:600 64px/1 sans-serif;color:rgba(255,255,255,.7);letter-spacing:.3em">
    <div style="position:absolute;inset:0;background-image:linear-gradient(rgba(255,255,255,.12) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.12) 1px,transparent 1px);background-size:100px 100px"></div>
    <span style="position:relative">TEST · ${label}</span></div></body>`);
  fs.mkdirSync(path.dirname(f), { recursive: true });
  await page.screenshot({ path: f, type: "jpeg", quality: 85 });
  console.log("wrote", f);
}
await browser.close();
