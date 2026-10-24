// Render Mermaid diagrams (.mmd) to PNG with a local copy of Mermaid, offline:
//   npm pack mermaid@11.4.1 && tar xzf mermaid-11.4.1.tgz     (once, anywhere)
//   MERMAID_JS=<that folder>/package/dist/mermaid.min.js node context/scripts/render-mermaid.cjs context/images/x.mmd …
// Each x.mmd becomes x.png beside it (white background, 2x for sharp text).
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const fs = require('fs');
(async () => {
  const lib = process.env.MERMAID_JS;
  if (!lib || !fs.existsSync(lib)) throw new Error('set MERMAID_JS to mermaid.min.js (see the top of this file)');
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
  const page = await browser.newPage({ deviceScaleFactor: 2, viewport: { width: 1400, height: 1000 } });
  await page.setContent('<html><body style="margin:0;background:#fff"><div id="out"></div></body></html>');
  await page.addScriptTag({ path: lib });
  await page.evaluate(() => mermaid.initialize({ startOnLoad: false, theme: 'default', securityLevel: 'strict', fontFamily: 'Arial, sans-serif' }));
  for (const file of process.argv.slice(2)) {
    const text = fs.readFileSync(file, 'utf8');
    const ok = await page.evaluate(async (src) => {
      const out = document.getElementById('out');
      try {
        const { svg } = await mermaid.render('d' + Math.random().toString(36).slice(2), src);
        out.innerHTML = svg;
        return 'ok';
      } catch (e) { return String(e.message || e); }
    }, text);
    if (ok !== 'ok') { console.error(`${file}: ${ok}`); process.exitCode = 1; continue; }
    const png = file.replace(/\.mmd$/, '.png');
    await page.locator('#out svg').screenshot({ path: png, omitBackground: false });
    console.log(`${file} -> ${png}`);
  }
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
