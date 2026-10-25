// Real-browser check of the K9.1 execution algos against the demo server (paper broker):
// iceberg, TWAP and POV started from the spec-drawn form, each watched to its end.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { openWidget } = require('./e2e-lib.cjs');
const { execSync } = require('child_process');
const out = process.argv[2] || '.';
const flat = (s) => s.replace(/\s+/g, ' ').trim();
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
  const page = await browser.newPage({ viewport: { width: 1680, height: 1000 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto('http://localhost:8000/');
  await page.getByLabel('Username').fill('demo');
  await page.getByLabel('Password').fill('demo-password-123');
  await page.getByLabel('Authenticator code').fill(execSync('python3 -m mocktrade.tools.demo --code').toString().trim());
  await page.keyboard.press('Enter');
  await page.locator('section.frame').first().waitFor({ timeout: 20000 });
  await page.evaluate(async () => {
    for (const k of Object.keys(localStorage)) localStorage.removeItem(k);
    await fetch('/api/layouts/current', { method: 'DELETE', credentials: 'same-origin' });
  });
  await page.reload();
  await openWidget(page, 'Algos');
  const algos = page.locator('.algos');
  await algos.waitFor();
  const form = algos.getByRole('form', { name: 'Start an algo' });
  const top = () => page.getByRole('grid', { name: 'Algos' }).getByRole('row').first();
  const open = async (code) => {
    if (!(await form.isVisible())) await algos.getByRole('button', { name: /New algo/ }).click();
    await form.getByRole('combobox', { name: 'Algo', exact: true }).selectOption(code);
    if (!(await form.locator('.algo-picked').count())) {
      await algos.getByRole('combobox', { name: 'Algo contract' }).fill('NIFTY26OCT');
      await algos.locator('.search-results [role=option]').first().click();
    }
  };
  const until = async (re, ms) => {
    const end = Date.now() + ms;
    while (Date.now() < end) {
      const t = flat(await top().innerText());
      if (re.test(t)) return t;
      await page.waitForTimeout(500);
    }
    return 'TIMEOUT: ' + flat(await top().innerText());
  };
  await algos.getByRole('button', { name: /New algo/ }).click();
  console.log('algo types:', (await form.getByRole('combobox', { name: 'Algo', exact: true }).locator('option').allInnerTexts()).join(', '));

  // 2) TWAP: 4 lots over 1 minute, deciding every 5 s, leaving nothing at the end
  await open('TWAP');
  console.log('twap fields:', (await form.locator('.algo-field > span:first-child').allInnerTexts()).join(' | '));
  await form.getByRole('textbox', { name: 'Lots', exact: true }).fill('4');
  await form.getByRole('textbox', { name: 'Over (min)' }).fill('1');
  await form.getByRole('textbox', { name: 'Decide every (s)' }).fill('5');
  await form.locator('.ticket-send').click();
  await page.waitForTimeout(1000);
  await page.waitForTimeout(15000);
  console.log('twap at 15 s:', flat(await top().innerText()));
  await page.screenshot({ path: `${out}/20-twap.png` });
  console.log('twap:', await until(/Done|Failed|Stopped/, 70000));
  await top().getByRole('button', { name: /Show .* details/ }).click();
  const twapAvg = Number(flat(await top().innerText()).split(' ')[7].replace(/,/g, ''));
  console.log('twap detail:', flat(await algos.locator('.algo-detail').first().innerText()));

  // 2b) iceberg: 6 lots shown 2 at a time, limit ₹20 above TWAP's price (fills slice by slice)
  await open('ICEBERG');
  console.log('iceberg fields:', (await form.locator('.algo-field > span:first-child').allInnerTexts()).join(' | '));
  await form.getByRole('textbox', { name: 'Lots', exact: true }).fill('6');
  await form.getByRole('textbox', { name: 'Show (lots)' }).fill('2');
  await form.getByRole('textbox', { name: 'Limit' }).fill(String(Math.round(twapAvg + 20)));
  await form.locator('.ticket-send').click();
  await page.waitForTimeout(1000);
  console.log('iceberg:', await until(/Done|Failed|Stopped/, 20000));
  await top().getByRole('button', { name: /Show .* details/ }).click();
  console.log('iceberg detail:', flat(await algos.locator('.algo-detail').first().innerText()));

  // 3) POV: 3 lots at 20% of the volume, crossing
  await open('POV');
  console.log('pov fields:', (await form.locator('.algo-field > span:first-child').allInnerTexts()).join(' | '));
  await form.getByRole('textbox', { name: 'Lots', exact: true }).fill('3');
  await form.getByRole('textbox', { name: 'Share' }).fill('20');
  await form.getByRole('textbox', { name: 'Max per order' }).fill('1');
  await form.getByRole('combobox', { name: 'Price' }).selectOption('cross');
  await form.locator('.ticket-send').click();
  await page.waitForTimeout(1000);
  await page.waitForTimeout(6000);
  console.log('pov at 6 s:', flat(await top().innerText()));
  console.log('pov:', await until(/Done|Failed|Stopped/, 90000));
  await top().getByRole('button', { name: /Show .* details/ }).click();
  console.log('pov detail:', flat(await algos.locator('.algo-detail').first().innerText()));
  await page.screenshot({ path: `${out}/21-exec-algos.png` });
  console.log('errors:', JSON.stringify(errors));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
