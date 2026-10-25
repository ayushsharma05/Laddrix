// Takes the screenshots the showcase video uses (#457), from a running demo:
//   python3 -m laddrix.tools.demo --port 8000 --ui ui/dist      (in another terminal)
//   node context/scripts/shoot-showcase.cjs                       → context/images/showcase/screens/
// The trading page as it opens, then each widget filling the page (a double-click on its title).
// autospreader, spread-chart, structures and algos.png are older shots with live spreads and algos,
// their top menu bar (the old name) trimmed off: a fresh demo has none to show.
const path = require('path');
const fs = require('fs');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { signIn, openWidget } = require('./e2e-lib.cjs');

const out = process.argv[2] || path.join(__dirname, '..', 'images', 'showcase', 'screens');
const WIDGETS = [
  ['ladder', 'Ladder', '.ladder'],
  ['ticket', 'Order Ticket', '.ticket'],
  ['chain', 'Option Chain'],
  ['strategy', 'Strategy Builder'],
  ['depth', 'Depth'],
  ['admin', 'Admin'],
];

(async () => {
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
  const page = await browser.newPage({ viewport: { width: 1680, height: 1000 }, colorScheme: 'dark' });
  await signIn(page);
  await page.evaluate(async () => {
    for (const k of Object.keys(localStorage)) localStorage.removeItem(k);
    await fetch('/api/layouts/current', { method: 'DELETE', credentials: 'same-origin' });
  });
  await page.reload();
  await page.locator('.ladder').first().waitFor({ timeout: 20000 });
  await page.waitForTimeout(6000); // prices, depth and volume fill in
  await page.screenshot({ path: path.join(out, 'trading.png') });
  console.log('drew trading.png');
  for (const [name, title, has] of WIDGETS) {
    let frame = await openWidget(page, title, { has: has ?? null });
    if (await page.getByLabel('Chart instrument').count()) {
      // a new chart asks for its contract first
      await page.getByLabel('Chart instrument').fill('NIFTY26OCT');
      await page.getByRole('option', { name: /NIFTY26OCTFUT/ }).first().click();
      frame = page.locator('section.frame:has(.chart)').last(); // its title is now the contract
    }
    const bar = frame.locator('.frame-title').first();
    await bar.dblclick();
    await page.waitForTimeout(2500); // the widget fills the page and its data arrives
    await page.screenshot({ path: path.join(out, `${name}.png`) });
    await bar.dblclick();
    await page.waitForTimeout(300);
    console.log(`drew ${name}.png`);
  }
  await browser.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
