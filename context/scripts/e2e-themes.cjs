// Real browser check of K13.5 on the demo (#285), with the demo up and ui/dist rebuilt: on a
// computer set to light, an unchosen theme shows light; picking Dark, Colour-blind and Light
// switches at once (no reload) and the chart repaints; the choice survives a reload (saved on the
// server); "Same as the computer" saves nothing. Screenshots of each theme go to context/images.
const { chromium } = require('/opt/node22/lib/node_modules/playwright');
const { execSync } = require('child_process');
const IMAGES = `${__dirname}/../images`;
(async () => {
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome' });
  const context = await browser.newContext({ viewport: { width: 1500, height: 950 }, colorScheme: 'light' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('http://localhost:8000/');
  await page.getByLabel('Username').fill('demo');
  await page.getByLabel('Password').fill('demo-password-123');
  await page.getByLabel('Authenticator code').fill(execSync('python3 -m mocktrade.tools.demo --code').toString().trim());
  await page.keyboard.press('Enter');
  await page.locator('.dv-tab').first().waitFor({ timeout: 20000 });
  await page.evaluate(async () => { await fetch('/api/me/settings/theme', { method: 'PUT', credentials: 'same-origin', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ value: null }) }); });
  await page.reload();
  await page.locator('.dv-tab').first().waitFor({ timeout: 20000 });
  await page.waitForTimeout(1500);
  await page.getByRole('button', { name: 'Widgets ▾' }).click();  // a chart, to see it repaint
  await page.getByRole('menuitem', { name: /^Chart/ }).click();
  if (await page.getByLabel('Chart instrument').count()) {
    await page.getByLabel('Chart instrument').fill('NIFTY26OCT');
    await page.getByRole('option', { name: /NIFTY26OCTFUT/ }).first().click();
  }
  await page.waitForFunction(() => /C \d/.test(document.querySelector('.chart [aria-label="Candle"]')?.textContent || ''), null, { timeout: 10000 });
  const shown = () => page.evaluate(() => ({ theme: document.documentElement.dataset.theme, body: getComputedStyle(document.body).backgroundColor }));
  console.log('nothing chosen, computer set to light:', JSON.stringify(await shown()));
  const pick = page.getByRole('combobox', { name: 'Theme' });
  const canvas = page.locator('.chart canvas').last();
  const paint = async () => ((await canvas.count()) ? canvas.evaluate((c) => {
    const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
    const seen = new Map();  // colours that cover at least 50 pixels (not edge blends)
    for (let i = 0; i < d.length; i += 4) { const k = `${d[i]},${d[i + 1]},${d[i + 2]}`; seen.set(k, (seen.get(k) || 0) + 1); }
    return [...seen].filter(([, n]) => n >= 50).map(([k]) => k).sort().join(' ');
  }) : 'no chart');
  for (const [value, file] of [['dark', 'dark'], ['cb', 'colour-blind'], ['light', 'light']]) {
    const before = await paint();
    const url = page.url();
    await pick.selectOption(value);
    await page.waitForTimeout(800);
    const now = await shown();
    console.log(`${value}:`, JSON.stringify(now), '| same page (no reload):', page.url() === url, '| chart repainted:', before !== (await paint()));
    await page.screenshot({ path: `${IMAGES}/k13-5-theme-${file}.png` });
  }
  await page.reload();
  await page.locator('.dv-tab').first().waitFor({ timeout: 20000 });
  await page.waitForTimeout(1000);
  const saved = await page.evaluate(async () => (await (await fetch('/api/me/settings/theme', { credentials: 'same-origin' })).json()).value);
  console.log('after a reload:', JSON.stringify(await shown()), 'saved:', JSON.stringify(saved), 'picker:', await pick.inputValue());
  await pick.selectOption('system');
  await page.waitForTimeout(500);
  const none = await page.evaluate(async () => (await (await fetch('/api/me/settings/theme', { credentials: 'same-origin' })).json()).value);
  console.log('Same as the computer:', JSON.stringify(await shown()), 'saved:', JSON.stringify(none));
  console.log('page errors:', errors.length ? errors : 'none');
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
