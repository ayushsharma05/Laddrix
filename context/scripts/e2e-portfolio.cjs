// Real browser check of K10.1 on the demo (paper broker): funds, holdings and the tradebook,
// over REST and on screen. Buys 75 futures, then reads Funds, the Trades tab and Holdings.
// Run against a fresh demo (rm -rf data/demo) with ui/dist rebuilt.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { openWidget } = require('./e2e-lib.cjs');
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
  const FUT = 'NFO:NIFTY26OCTFUT';
  const before = await api('GET', `${A}/funds?fresh=true`);
  const f0 = before.body.items[0];
  console.log('funds before:', before.status, f0.available_paise, f0.used_paise, 'estimate', f0.estimate);
  const order = await api('POST', `${A}/orders`, { instrument_id: FUT, side: 'BUY', qty: 75, order_type: 'MARKET', product: 'NRML', market_protection: -1 });
  console.log('order:', order.status, order.body && (order.body.status || order.body.detail));
  await page.waitForTimeout(2500); // the fill, then the debounced refresh pushes
  const after = await api('GET', `${A}/funds`);
  const f1 = after.body.items[0];
  console.log('funds after:', f1.available_paise, 'used', f1.used_paise, JSON.stringify(f1.used_lines));
  const trades = await api('GET', `${A}/trades`);
  console.log('trades:', JSON.stringify(trades.body.items.map((t) => [t.trade_id, t.client_order_id, t.side, t.qty, t.price_paise])));
  const holdings = await api('GET', `${A}/holdings`);
  console.log('holdings:', holdings.status, holdings.body.items.length, 'as_of', !!holdings.body.as_of);
  // on screen: the Trades tab, then Funds and Holdings beside Positions
  await page.getByRole('button', { name: /^Trades/ }).click();
  const tradeRow = page.getByRole('grid', { name: 'Trades' }).getByRole('row').first();
  console.log('trades tab:', (await tradeRow.textContent()).replace(/\s+/g, ' '));
  await openWidget(page, 'Funds');
  const seg = page.getByRole('region', { name: 'equity funds' });
  await seg.waitFor({ timeout: 5000 });
  console.log('funds screen:', (await seg.textContent()).replace(/\s+/g, ' ').slice(0, 160));
  await openWidget(page, 'Holdings');
  console.log('holdings screen:', (await page.getByRole('grid', { name: 'Holdings' }).textContent()).trim());
  await page.screenshot({ path: '/tmp/claude-0/e2e-portfolio.png' });
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
