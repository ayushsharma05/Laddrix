// Real browser check of K10.5 on a fresh demo (paper): an MIS position, the Positions bar's
// "Square off MIS" button, its confirmation and the position closing; then an NRML position
// with an MTM loss limit of ₹1 and auto-close set from the MTM Limits tab, closed on the next
// price, and the message it leaves. Run against a fresh demo with ui/dist rebuilt.
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
  const net = async (product) => ((await api('GET', `${A}/positions`)).body.positions.find((p) => p.instrument_id === FUT && p.product === product) || { qty: 0 }).qty;
  const buy = await api('POST', `${A}/orders`, { instrument_id: FUT, side: 'BUY', qty: 75, order_type: 'MARKET', product: 'MIS', market_protection: -1 });
  console.log('buy MIS:', buy.status, buy.body.status);
  await page.waitForTimeout(1500);
  await page.locator('.dv-tab', { hasText: 'Positions' }).first().click();
  const button = page.getByRole('button', { name: /Square off MIS \(1\)/ });
  await button.waitFor({ timeout: 35000 }); // the button asks the server (every 30 s, and on changes)
  console.log('button:', await button.textContent(), '|', await button.getAttribute('title'));
  await button.click();
  await page.getByRole('button', { name: 'Confirm: close 1 MIS' }).click();
  await page.waitForTimeout(2000);
  console.log('MIS after square-off:', await net('MIS'));
  // MTM: an NRML position with a ₹1 loss limit and auto-close, set on the MTM Limits tab
  const nrml = await api('POST', `${A}/orders`, { instrument_id: FUT, side: 'BUY', qty: 75, order_type: 'MARKET', product: 'NRML', market_protection: -1 });
  console.log('buy NRML:', nrml.status, nrml.body.status);
  await page.waitForTimeout(1500);
  await page.locator('.dv-tab', { hasText: 'MTM Limits' }).click();
  await page.getByLabel('NIFTY26OCTFUT loss limit').fill('1');
  await page.getByLabel('NIFTY26OCTFUT auto-close').check();
  await page.getByRole('grid', { name: 'Position limits' }).getByRole('button', { name: 'Set' }).click();
  await page.waitForTimeout(4000); // the next prices
  console.log('NRML after the MTM limit:', await net('NRML'));
  const rules = await api('GET', `${A}/mtm`);
  console.log('rule:', JSON.stringify(rules.body.positions[0]));
  await page.locator('.dv-tab', { hasText: 'Messages' }).click();
  console.log('message:', (await page.getByRole('grid', { name: 'Messages' }).getByText(/reached the loss limit/).first().textContent()).slice(0, 160));
  await page.screenshot({ path: '/tmp/claude-0/e2e-mtm.png' });
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
