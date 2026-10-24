// Real browser check of #262 on a fresh demo (paper), with ui/dist rebuilt: an MIS position and
// a resting order; STOP from the toolbar; the red band says what the STOP did in the server's
// words and offers Flatten; an ordinary order is still refused; Flatten needs two clicks,
// closes the position while trading stays stopped, and leaves its message.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { execSync } = require('child_process');
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  await page.goto('http://localhost:8000/');
  await page.getByLabel('Username').fill('demo');
  await page.getByLabel('Password').fill('demo-password-123');
  await page.getByLabel('Authenticator code').fill(execSync('python3 -m mocktrade.tools.demo --code').toString().trim());
  await page.keyboard.press('Enter');
  await page.locator('.dv-tab').first().waitFor({ timeout: 20000 });
  const api = (m, p, b) => page.evaluate(async ([m, p, b]) => {
    const r = await fetch(p, { method: m, credentials: 'same-origin', headers: { 'content-type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return { status: r.status, body: await r.json().catch(() => null) };
  }, [m, p, b]);
  const A = '/api/accounts/acc-paper';
  const FUT = (await api('GET', '/api/instruments?q=NIFTY')).body.find((i) => /^NFO:NIFTY\d.*FUT$/.test(i.id)).id;
  const net = async () => (await api('GET', `${A}/positions`)).body.positions.filter((p) => p.instrument_id === FUT).reduce((s, p) => s + p.qty, 0);
  const buy = await api('POST', `${A}/orders`, { instrument_id: FUT, side: 'BUY', qty: 150, order_type: 'MARKET', product: 'MIS', market_protection: -1 });
  console.log('buy 150 MIS:', buy.status, buy.body.status);
  await page.waitForTimeout(1500);
  const pos = (await api('GET', `${A}/positions`)).body.positions.find((p) => p.product === 'MIS');
  const rest = await api('POST', `${A}/orders`, { instrument_id: FUT, side: 'SELL', qty: 75, order_type: 'LIMIT', product: 'MIS', price_paise: (Math.floor(Number(pos.avg_price_paise) / 1000) + 20) * 1000 });
  console.log('resting target:', rest.body.request.client_order_id, rest.body.status);
  await page.getByRole('button', { name: 'STOP', exact: true }).click();
  await page.getByRole('button', { name: 'STOP NOW' }).click();
  const band = page.locator('.stop-band');
  await band.getByText(/working order asked to cancel/).waitFor({ timeout: 10000 });
  console.log('band:', (await band.innerText()).replace(/\s+/g, ' '));
  await page.screenshot({ path: process.env.SHOT || '/tmp/flatten-band.png', clip: { x: 0, y: 0, width: 1600, height: 140 } });
  const plain = await api('POST', `${A}/orders`, { instrument_id: FUT, side: 'SELL', qty: 150, order_type: 'MARKET', product: 'MIS', market_protection: -1 });
  console.log('ordinary close while stopped:', plain.body.status, '-', plain.body.reason);
  await band.getByRole('button', { name: 'Flatten (1)' }).click();
  console.log('after one click:', await band.getByRole('button').innerText(), '| position', await net());
  await band.getByRole('button', { name: 'Confirm: close 1 position' }).click();
  await page.waitForTimeout(2000);
  const status = (await api('GET', `${A}/flatten`)).body;
  console.log('after flatten: position', await net(), '| still stopped:', status.stopped, '| last:', status.last.summary);
  const msg = await page.evaluate(() => [...document.querySelectorAll('.messages .row, [role=log] *')].map((e) => e.textContent).find((t) => t && t.includes('Flatten:')));
  console.log('message:', msg || '(Messages widget not open)');
  const ok = (await net()) === 0 && status.stopped && plain.body.status === 'REJECTED';
  console.log(ok ? 'PASS' : 'FAIL');
  await browser.close();
  process.exit(ok ? 0 : 1);
})();
