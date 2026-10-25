// Real-browser check of the algo manager against the demo server (paper broker):
// start a slicer and a spreader from the spec-drawn form, watch them, pause/resume/stop.
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
  await page.locator('.ladder').first().waitFor({ timeout: 20000 });
  await page.evaluate(async () => {
    for (const k of Object.keys(localStorage)) localStorage.removeItem(k);
    await fetch('/api/layouts/current', { method: 'DELETE', credentials: 'same-origin' });
  });
  await page.reload();
  await page.locator('.ladder').first().waitFor({ timeout: 20000 });
  await openWidget(page, 'Algos');
  const algos = page.locator('.algos');
  await algos.waitFor();
  const grid = () => page.getByRole('grid', { name: 'Algos' }).getByRole('row').allInnerTexts();
  const pick = async (label, q) => {
    await algos.getByRole('combobox', { name: label }).fill(q);
    await algos.locator('.search-results [role=option]').first().click();
  };

  // 1) slicer: 4 lots in 2 slices a second apart, crossing the spread
  await algos.getByRole('button', { name: /New algo/ }).click();
  const form = algos.getByRole('form', { name: 'Start an algo' });
  console.log('algo types:', (await form.getByRole('combobox', { name: 'Algo', exact: true }).locator('option').allInnerTexts()).join(', '));
  console.log('slicer fields:', (await form.locator('.algo-field > span:first-child').allInnerTexts()).join(' | '));
  await pick('Algo contract', 'NIFTY26OCT');
  await form.getByRole('textbox', { name: 'Lots' }).fill('4');
  await form.getByRole('textbox', { name: 'Slices' }).fill('2');
  await form.getByRole('textbox', { name: 'Every (s)' }).fill('1');
  await form.getByRole('combobox', { name: 'Price' }).selectOption('cross');
  await page.screenshot({ path: `${out}/10-algo-form.png` });
  await form.locator('.ticket-send').click();
  await page.waitForTimeout(3500);
  console.log('after slicer:', (await grid()).map(flat).join(' / '));
  await page.getByRole('grid', { name: 'Algos' }).getByRole('row').first().click();

  // 2) spreader: buy OCT − NOV at a level the market already offers, so it trades and hedges
  await algos.getByRole('button', { name: /New algo/ }).click();
  await form.getByRole('combobox', { name: 'Algo', exact: true }).selectOption('SPREAD');
  console.log('spread fields:', (await form.locator('.algo-field > span:first-child').allInnerTexts()).join(' | '));
  console.log('contract kept:', flat(await form.locator('.algo-picked').first().innerText()));
  await pick('Leg B', 'NIFTY26NOV');
  await form.getByRole('textbox', { name: /Spread/ }).fill('-50');
  await form.locator('.ticket-send').click();
  await page.waitForTimeout(1500);
  const top = page.getByRole('grid', { name: 'Algos' }).getByRole('row').first();
  console.log('spreader:', flat(await top.innerText()));
  await top.getByRole('button', { name: /Show .* details/ }).click();
  console.log('detail:', flat(await algos.locator('.algo-detail').first().innerText()));

  // 3) pause / resume / stop on the spreader if still working
  await top.click();
  const state = async () => flat(await top.innerText());
  if (!(await algos.getByRole('button', { name: 'Pause', exact: true }).isDisabled())) {
    await algos.getByRole('button', { name: 'Pause', exact: true }).click();
    await page.waitForTimeout(600);
    console.log('paused:', await state());
    await algos.getByRole('button', { name: 'Resume', exact: true }).click();
    await page.waitForTimeout(600);
    console.log('resumed:', await state());
    await algos.getByRole('button', { name: 'Stop', exact: true }).click();
    await page.waitForTimeout(1000);
    console.log('stopped:', await state());
  }
  await page.screenshot({ path: `${out}/11-algos.png` });
  const msgs = await page.evaluate(() => 0);
  await openWidget(page, 'Messages');
  console.log('messages:', (await page.getByRole('grid', { name: 'Messages' }).getByRole('row').allInnerTexts()).slice(0, 6).map(flat).join(' / '));
  console.log('errors:', JSON.stringify(errors), msgs);
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
