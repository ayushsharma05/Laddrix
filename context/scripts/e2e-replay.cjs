// Real browser check of K11.2: a replay server (python -m mocktrade.tools.demo --replay <day>
// --port 8001) on a day the demo recorded. The replay bar and band, prices moving on the
// replayed time, an order filled by the paper broker, pause/resume, speed and jump ahead.
const { chromium } = require('/opt/node22/lib/node_modules/playwright');
const { execSync } = require('child_process');
const day = process.argv[2];
(async () => {
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome' });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  await page.goto('http://localhost:8001/');
  await page.getByLabel('Username').fill('demo');
  await page.getByLabel('Password').fill('demo-password-123');
  await page.getByLabel('Authenticator code').fill(execSync(`cd /home/user/MockTrade && python3 -m mocktrade.tools.demo --replay ${day} --code`).toString().trim());
  await page.keyboard.press('Enter');
  await page.locator('.dv-tab').first().waitFor({ timeout: 20000 });
  const bar = page.getByRole('region', { name: 'Replay' });
  await bar.waitFor({ timeout: 5000 });
  console.log('bar:', (await bar.textContent()).replace(/\s+/g, ' '));
  console.log('band:', (await page.locator('.band').filter({ hasText: 'REPLAY' }).first().textContent()).replace(/\s+/g, ' ').slice(0, 120));
  const api = (m, p, b) => page.evaluate(async ([m, p, b]) => {
    const r = await fetch(p, { method: m, credentials: 'same-origin', headers: { 'content-type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return { status: r.status, body: await r.json().catch(() => null) };
  }, [m, p, b]);
  const s1 = (await api('GET', '/api/replay')).body;
  await page.waitForTimeout(2000);
  const s2 = (await api('GET', '/api/replay')).body;
  console.log('replayed time moves:', s1.at, '→', s2.at, 'fed', s1.fed, '→', s2.fed);
  const o = await api('POST', '/api/accounts/acc-paper/orders', { instrument_id: 'NFO:NIFTY26OCTFUT', side: 'BUY', qty: 75, order_type: 'MARKET', product: 'NRML', market_protection: -1 });
  console.log('order:', o.status, o.body.status);
  await page.waitForTimeout(2000);
  const pos = (await api('GET', '/api/accounts/acc-paper/positions')).body.positions.find((p) => p.instrument_id === 'NFO:NIFTY26OCTFUT');
  console.log('position:', pos && pos.qty, 'created at (replayed time):', (await api('GET', '/api/accounts/acc-paper/orders')).body[0].created_at);
  await page.getByRole('button', { name: 'Pause' }).click();
  const paused = (await api('GET', '/api/replay')).body;
  await page.waitForTimeout(1500);
  const still = (await api('GET', '/api/replay')).body;
  console.log('paused:', paused.state, 'fed', paused.fed, '→', still.fed);
  await page.getByRole('button', { name: 'Resume' }).click();
  await page.getByRole('button', { name: 'max' }).click();
  await page.getByRole('button', { name: '+15 min' }).click();
  await page.waitForTimeout(1500);
  console.log('after max + jump:', JSON.stringify((await api('GET', '/api/replay')).body));
  await page.screenshot({ path: '/tmp/claude-0/e2e-replay.png' });
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
