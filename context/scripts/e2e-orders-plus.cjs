// Real-browser check of the K9.4 screens on the demo (paper broker): the GTT book's form
// (drawn from the GTT spec) places an OCO; the ticket's Algo tab starts a trailing stop and
// its GTT tab places a single GTT; the algo picker is grouped by family.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
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
  await page.locator('.dv-tab').first().waitFor({ timeout: 20000 });
  await page.evaluate(async () => {
    for (const k of Object.keys(localStorage)) localStorage.removeItem(k);
    await fetch('/api/layouts/current', { method: 'DELETE', credentials: 'same-origin' });
  });
  await page.reload();
  await page.locator('.dv-tab', { hasText: 'GTT Book' }).waitFor({ timeout: 20000 });

  // 1) GTT book: New GTT, OCO, 2 lots
  await page.locator('.dv-tab', { hasText: 'GTT Book' }).click();
  const book = page.locator('.gtts');
  await book.getByRole('button', { name: /New GTT/ }).click();
  const form = book.getByRole('form', { name: 'Place a GTT' });
  if (await form.getByRole('combobox', { name: 'GTT contract' }).count()) {
    await form.getByRole('combobox', { name: 'GTT contract' }).fill('NIFTY26OCTFUT');
    await page.locator('.search-results [role=option]').first().click();
  }
  await form.getByRole('combobox', { name: 'GTT kind' }).selectOption('two-leg');
  console.log('kinds:', (await form.getByRole('combobox', { name: 'GTT kind' }).locator('option').allInnerTexts()).join(' | '));
  console.log('help:', flat(await form.locator('.algo-help').innerText()));
  await form.getByRole('textbox', { name: 'GTT lots' }).fill('2');
  console.log('stop/target:', await form.getByRole('textbox', { name: 'Stop (lower) trigger' }).inputValue(), '/', await form.getByRole('textbox', { name: 'Target (upper) trigger' }).inputValue());
  await form.getByRole('button', { name: /Place GTT/ }).click();
  await page.waitForTimeout(800);
  console.log('gtt rows:', (await page.getByRole('grid', { name: 'GTTs' }).getByRole('row').allInnerTexts()).map(flat).join(' / '));
  await page.screenshot({ path: `${out}/23-gtt-book.png` });

  // 2) ticket: Algo tab -> trailing stop from the ticket
  const ticket = page.locator('.ticket').first();
  console.log('ticket tabs:', (await ticket.getByRole('tab').allInnerTexts()).join(' | '));
  await ticket.getByRole('tab', { name: 'Algo' }).click();
  const algoForm = page.getByRole('form', { name: 'Start an algo' }).first();
  await algoForm.waitFor();
  console.log('algo groups:', JSON.stringify(await algoForm.locator('optgroup').evaluateAll((g) => g.map((x) => x.label))));
  await algoForm.getByRole('combobox', { name: 'Algo', exact: true }).selectOption('TRAILING_STOP');
  await algoForm.getByRole('group', { name: 'Side' }).getByRole('button', { name: 'SELL' }).click();
  await algoForm.getByRole('textbox', { name: 'Trail (points)' }).fill('25');
  await algoForm.locator('.ticket-send').click();
  await page.waitForTimeout(1000);
  await page.locator('.dv-tab', { hasText: 'Algos' }).click();
  console.log('algo row:', flat(await page.getByRole('grid', { name: 'Algos' }).getByRole('row').first().innerText()));

  // 3) ticket: GTT tab
  await page.locator('.ticket').first().getByRole('tab', { name: 'GTT' }).click();
  const gttForm = page.locator('.ticket').first().getByRole('form', { name: 'Place a GTT' });
  await gttForm.getByRole('button', { name: /Place GTT/ }).waitFor();
  console.log('ticket gtt button:', flat(await gttForm.getByRole('button', { name: /Place GTT/ }).innerText()));
  await gttForm.getByRole('button', { name: /Place GTT/ }).click();
  await page.waitForTimeout(800);
  await page.locator('.dv-tab', { hasText: 'GTT Book' }).click();
  console.log('gtt rows after ticket:', (await page.getByRole('grid', { name: 'GTTs' }).getByRole('row').allInnerTexts()).map(flat).join(' / '));
  await page.screenshot({ path: `${out}/24-orders-plus.png` });
  console.log('messages:', (await page.evaluate(() => 0)));
  console.log('errors:', JSON.stringify(errors));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
