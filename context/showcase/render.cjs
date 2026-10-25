// Photographs every poster in this folder into context/images/showcase/ (#455).
//   node context/showcase/render.cjs            all posters
//   node context/showcase/render.cjs 02-arch    only the ones whose name starts so
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const here = __dirname;
const out = path.join(here, "..", "images", "showcase");
const only = process.argv[2] ?? "";

(async () => {
  fs.mkdirSync(out, { recursive: true });
  const pages = fs.readdirSync(here).filter((f) => f.endsWith(".html") && f.startsWith(only)).sort();
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, deviceScaleFactor: 2 });
  for (const name of pages) {
    await page.goto("file://" + path.join(here, name));
    await page.evaluate(() => document.fonts.ready);
    const file = path.join(out, name.replace(/\.html$/, ".png"));
    await page.screenshot({ path: file });
    console.log("drew", path.relative(process.cwd(), file));
  }
  await browser.close();
})();
