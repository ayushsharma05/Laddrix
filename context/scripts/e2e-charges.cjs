// Real browser check of K10.2 on a fresh demo (paper): buy then sell 75 futures, then read the
// day's charges (REST), Positions (gross, charges, net) and the ticket's estimate on screen.
// Run against a fresh demo (rm -rf data/demo) with ui/dist rebuilt.
const { chromium } = require('/opt/node22/lib/node_modules/playwright');
const { execSync } = require('child_process');
(async () => {
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome' });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  await page.goto('http://localhost:8000/');
  await page.getByLabel('Username').fill('demo');
  await page.getByLabel('Password').fill('demo-password-123');
  await page.getByLabel('Authenticator code').fill(execSync('cd /home/user/MockTrade && python3 -m mocktrade.tools.demo --code').toString().trim());
  await page.keyboard.press('Enter');
  await page.locator('.dv-tab').first().waitFor({ timeout: 20000 });
  const api = (m, p, b) => page.evaluate(async ([m, p, b]) => {
    const r = await fetch(p, { method: m, credentials: 'same-origin', headers: { 'content-type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return { status: r.status, body: await r.json().catch(() => null) };
  }, [m, p, b]);
  const A = '/api/accounts/acc-paper';
  const FUT = 'NFO:NIFTY26OCTFUT';
  const schedule = await api('GET', '/api/charges/schedule');
  console.log('schedule:', schedule.body.name, schedule.body.checked_on, 'futures STT sell', schedule.body.segments.futures.stt_sell_pct);
  for (const side of ['BUY', 'SELL']) {
    const o = await api('POST', `${A}/orders`, { instrument_id: FUT, side, qty: 75, order_type: 'MARKET', product: 'NRML', market_protection: -1 });
    console.log(side, o.status, o.body && (o.body.status || o.body.detail));
    await page.waitForTimeout(1500);
  }
  const today = await api('GET', `${A}/charges`);
  console.log('charges today:', today.body.total_paise, JSON.stringify(today.body.lines.filter((l) => l.paise)), JSON.stringify(today.body.by_position));
  const bar = await page.locator('.book-bar', { hasText: 'Day gross' }).textContent();
  console.log('positions bar:', bar.replace(/\s+/g, ' '));
  const row = await page.getByRole('grid', { name: 'Positions' }).getByRole('row').first().textContent();
  console.log('positions row:', row.replace(/\s+/g, ' '));
  const summary = page.getByLabel('Order summary');
  await page.waitForFunction(() => /Charges ≈ ₹/.test(document.querySelector('[aria-label="Order summary"]')?.textContent ?? ''), null, { timeout: 5000 });
  console.log('ticket:', (await summary.textContent()).replace(/\s+/g, ' '));
  await page.screenshot({ path: '/tmp/claude-0/e2e-charges.png' });
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
