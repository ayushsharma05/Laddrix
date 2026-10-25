// Real browser check of K10.4 on a fresh demo (paper, ₹10 lakh): the ticket's margin line for
// 1 lot and for 5 lots (more than the funds: red); then 2 + 2 lots bought (about ₹9 lakh of
// margin), 1 more refused with both numbers, and the exit (2 + 2 lots) always accepted. Orders
// stay at 2 lots: bigger ones hit the ₹50 lakh value limit per order first.
// Run against a fresh demo (rm -rf data/demo) with ui/dist rebuilt.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { execSync } = require('child_process');
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  await page.goto('http://localhost:8000/');
  await page.getByLabel('Username').fill('demo');
  await page.getByLabel('Password').fill('demo-password-123');
  await page.getByLabel('Authenticator code').fill(execSync('python3 -m laddrix.tools.demo --code').toString().trim());
  await page.keyboard.press('Enter');
  await page.locator('section.frame').first().waitFor({ timeout: 20000 });
  const api = (m, p, b) => page.evaluate(async ([m, p, b]) => {
    const r = await fetch(p, { method: m, credentials: 'same-origin', headers: { 'content-type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return { status: r.status, body: await r.json().catch(() => null) };
  }, [m, p, b]);
  const A = '/api/accounts/acc-paper';
  const FUT = 'NFO:NIFTY26NOVFUT'; // the ticket's contract in the default layout
  const summary = page.getByLabel('Order summary');
  const marginLine = async () => {
    await page.waitForFunction(() => /Margin/.test(document.querySelector('[aria-label="Order summary"]')?.textContent ?? ''), null, { timeout: 5000 });
    const el = summary.locator('span', { hasText: 'Margin' }).first();
    return `${(await el.textContent()).trim()} [${await el.getAttribute('class')}]`;
  };
  console.log('ticket 1 lot:', await marginLine());
  const lots = page.getByRole('textbox', { name: /lots/i }).first();
  await lots.fill('5');
  await page.waitForTimeout(800);
  console.log('ticket 5 lots:', await marginLine());
  const order = (side, qty, type = 'MARKET') => api('POST', `${A}/orders`, { instrument_id: FUT, side, qty, order_type: type, product: 'NRML', market_protection: type === 'MARKET' ? -1 : null, price_paise: type === 'LIMIT' ? 2514860 : null });
  for (const n of [1, 2]) {
    const r = await order('BUY', 150);
    console.log(`2 lots (${n}):`, r.status, r.body && (r.body.status || r.body.reason));
    await page.waitForTimeout(1500); // the fill, then the funds refresh
  }
  const funds = await api('GET', `${A}/funds`);
  console.log('available now:', funds.body.items[0].available_paise);
  const more = await order('BUY', 75, 'LIMIT');
  console.log('1 more lot:', more.status, more.body && (more.body.reason || more.body.detail || more.body.status));
  for (const n of [1, 2]) {
    const r = await order('SELL', 150);
    console.log(`exit 2 lots (${n}):`, r.status, r.body && `${r.body.status} ${r.body.reason || ''}`);
    await page.waitForTimeout(1500); // two identical orders within 1 s count as a double click
  }
  await page.waitForTimeout(1500);
  const notes = await api('GET', `${A}/positions`);
  console.log('net after exit:', JSON.stringify(notes.body.positions.map((p) => [p.instrument_id, p.qty])));
  await page.screenshot({ path: '/tmp/claude-0/e2e-margin.png' });
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
