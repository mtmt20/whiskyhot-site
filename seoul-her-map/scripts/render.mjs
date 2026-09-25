/** out/<note>/cards/*.html → PNG (1242x1656). 전역 설치된 playwright + 사전 설치된 Chromium 사용. */
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { execSync } from "node:child_process";

async function loadPlaywright() {
  try { return await import("playwright"); } catch {}
  const g = execSync("npm root -g").toString().trim();
  return createRequire(import.meta.url)(path.join(g, "playwright"));
}

export async function renderDir(cardsDir) {
  const { chromium } = await loadPlaywright();
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
  const page = await browser.newPage({ viewport: { width: 1242, height: 1656 }, deviceScaleFactor: 1 });
  const files = fs.readdirSync(cardsDir).filter(f => f.endsWith(".html")).sort();
  for (const f of files) {
    await page.goto("file://" + path.join(cardsDir, f), { waitUntil: "networkidle", timeout: 20000 }).catch(() => {});
    await page.evaluate(() => document.fonts.ready).catch(() => {});
    const out = path.join(cardsDir, f.replace(/\.html$/, ".png"));
    await page.screenshot({ path: out, clip: { x: 0, y: 0, width: 1242, height: 1656 } });
    console.log("  rendered", path.basename(out));
  }
  await browser.close();
}

if (process.argv[1] && process.argv[1].endsWith("render.mjs")) {
  const dir = process.argv[2];
  if (!dir) { console.error("usage: node scripts/render.mjs out/<note>/cards"); process.exit(1); }
  renderDir(path.resolve(dir));
}
