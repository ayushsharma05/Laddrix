// Real browser check of K10.3 on a fresh demo (paper): a futures round trip, then the Reports
// tab (today's preview: totals, positions, strategies), both downloads saved by the browser and
// read back (CSV text, PDF header and page count), and the History tab.
// Run against a fresh demo (rm -rf data/demo) with ui/dist rebuilt.
const { chromium } = require('/opt/node22/lib/node_modules/playwright');
const { execSync } = require('child_process');
const fs = require('fs');
(async () => {
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome' });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, acceptDownloads: true });
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
  for (const side of ['BUY', 'SELL']) {
    const o = await api('POST', `${A}/orders`, { instrument_id: 'NFO:NIFTY26OCTFUT', side, qty: 75, order_type: 'MARKET', product: 'NRML', market_protection: -1 });
    console.log(side, o.status);
    await page.waitForTimeout(1200);
  }
  await page.locator('.dv-tab', { hasText: 'Reports' }).click();
  const totals = page.getByLabel('Day totals');
  await totals.waitFor({ timeout: 5000 });
  console.log('state:', await page.locator('.reports .book-bar span[class^="tone-"]').first().textContent());
  console.log('totals:', (await totals.textContent()).replace(/\s+/g, ' '));
  console.log('position:', (await page.getByRole('grid', { name: 'Report positions' }).getByRole('row').first().textContent()).replace(/\s+/g, ' '));
  for (const fmt of ['CSV', 'PDF']) {
    const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('link', { name: fmt }).click()]);
    const file = `/tmp/claude-0/${download.suggestedFilename()}`;
    await download.saveAs(file);
    const bytes = fs.readFileSync(file);
    if (fmt === 'CSV') console.log('csv:', download.suggestedFilename(), bytes.toString().split('\n').slice(0, 9).join(' | '));
    else console.log('pdf:', download.suggestedFilename(), bytes.slice(0, 8).toString(), 'pages', (bytes.toString('latin1').match(/\/Count (\d+)/) || [])[1]);
  }
  await page.screenshot({ path: '/tmp/claude-0/e2e-reports.png' });
  await page.getByRole('button', { name: 'History' }).click();
  await page.getByLabel('Period totals').waitFor({ timeout: 5000 });
  console.log('history:', (await page.getByLabel('Period totals').textContent()).replace(/\s+/g, ' '));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
