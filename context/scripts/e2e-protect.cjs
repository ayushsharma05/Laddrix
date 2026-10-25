// Real-browser check of K9.2 protection algos against the demo (paper broker): a bracket
// entered at the market gets its target at once; a trailing stop on the position follows.
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
  await page.getByLabel('Authenticator code').fill(execSync('python3 -m laddrix.tools.demo --code').toString().trim());
  await page.keyboard.press('Enter');
  await page.locator('section.frame').first().waitFor({ timeout: 20000 });
  await page.evaluate(async () => {
    for (const k of Object.keys(localStorage)) localStorage.removeItem(k);
    await fetch('/api/layouts/current', { method: 'DELETE', credentials: 'same-origin' });
  });
  await page.reload();
  await openWidget(page, 'Algos');
  const algos = page.locator('.algos');
  const form = algos.getByRole('form', { name: 'Start an algo' });
  const top = () => page.getByRole('grid', { name: 'Algos' }).getByRole('row').first();
  await algos.getByRole('button', { name: /New algo/ }).click();
  console.log('algo types:', (await form.getByRole('combobox', { name: 'Algo', exact: true }).locator('option').allInnerTexts()).join(', '));
  const pick = async (code) => {
    await form.getByRole('combobox', { name: 'Algo', exact: true }).selectOption(code);
    if (!(await form.locator('.algo-picked').count())) {
      await algos.getByRole('combobox', { name: 'Algo contract' }).fill('NIFTY26OCT');
      await algos.locator('.search-results [role=option]').first().click();
    }
  };
  // 1) bracket: buy 2 lots at the market, target +20, stop -15
  await pick('BRACKET');
  console.log('bracket fields:', (await form.locator('.algo-field > span:first-child').allInnerTexts()).join(' | '));
  await form.getByRole('textbox', { name: 'Lots', exact: true }).fill('2');
  await form.getByRole('textbox', { name: 'Target (points)' }).fill('20');
  await form.getByRole('textbox', { name: 'Stop (points)' }).fill('15');
  await form.locator('.ticket-send').click();
  await page.waitForTimeout(3000);
  console.log('bracket:', flat(await top().innerText()));
  await top().getByRole('button', { name: /Show .* details/ }).click();
  console.log('bracket detail:', flat(await algos.locator('.algo-detail').first().innerText()));
  // 2) trailing stop protecting a long of 2 lots, 10 points
  if (!(await form.isVisible())) await algos.getByRole('button', { name: /New algo/ }).click();
  await pick('TRAILING_STOP');
  console.log('trailing fields:', (await form.locator('.algo-field > span:first-child').allInnerTexts()).join(' | '));
  await form.getByRole('combobox', { name: 'Side' }).selectOption('SELL').catch(async () => {
    await form.getByRole('button', { name: /SELL/i }).first().click();
  });
  await form.getByRole('textbox', { name: 'Lots', exact: true }).fill('1');
  await form.getByRole('textbox', { name: 'Trail (points)' }).fill('10');
  await form.locator('.ticket-send').click();
  await page.waitForTimeout(1000);
  let last = '';
  for (let i = 0; i < 40; i++) {
    const t = flat(await top().innerText());
    if (t !== last) { console.log('trailing:', t); last = t; }
    if (/Done|Failed|Stopped/.test(t)) break;
    await page.waitForTimeout(1500);
  }
  await page.screenshot({ path: `${out}/22-protect.png` });
  console.log('errors:', JSON.stringify(errors));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
