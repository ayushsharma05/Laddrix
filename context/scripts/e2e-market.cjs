// Real browser check of K12.4 on the demo: Depth, Time & Sales and Movers from Widgets on the
// pink link (NIFTY future), their numbers against the server's own answers, then the Alerts
// widget: a webhook refused for http, an alert made from the New form just above the live price
// that fires (its row and Messages), switched on again, and deleted after the second click.
// Run with the demo up and ui/dist rebuilt.
const { chromium } = require('/opt/node22/lib/node_modules/playwright');
const { execSync } = require('child_process');
const flat = (s) => s.replace(/\s+/g, ' ').trim();
(async () => {
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome' });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('http://localhost:8000/');
  await page.getByLabel('Username').fill('demo');
  await page.getByLabel('Password').fill('demo-password-123');
  await page.getByLabel('Authenticator code').fill(execSync('cd /home/user/MockTrade && python3 -m mocktrade.tools.demo --code').toString().trim());
  await page.keyboard.press('Enter');
  await page.locator('.dv-tab').first().waitFor({ timeout: 20000 });
  const api = (m, p, b) => page.evaluate(async ([m, p, b]) => {
    const r = await fetch(p, { method: m, credentials: 'same-origin', headers: { 'content-type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return r.status === 204 ? null : r.json();
  }, [m, p, b]);
  await page.evaluate(async () => { await fetch('/api/layouts/current', { method: 'DELETE', credentials: 'same-origin' }); });
  for (const a of await api('GET', '/api/alerts')) await api('DELETE', `/api/alerts/${a.alert_id}`);
  await api('DELETE', '/api/alerts/webhook');  // start without one
  await page.reload();
  await page.locator('.ladder').first().waitFor({ timeout: 20000 });
  const open = async (name, search) => {
    await page.getByRole('button', { name: 'Widgets ▾' }).click();
    await page.getByRole('menuitem', { name: new RegExp(`^${name}`) }).click();
    await page.waitForTimeout(400);
    if (search && (await page.getByLabel(search).count())) {  // no contract yet: pick it
      await page.getByLabel(search).fill('NIFTY26OCT');
      await page.getByRole('option', { name: /NIFTY26OCTFUT/ }).first().click();
    }
  };
  // the pink group shows NIFTY26OCTFUT (a watch-list click)

  await open('Depth', 'Depth instrument');
  const depthGrid = page.getByRole('grid', { name: 'Depth' });
  await depthGrid.getByRole('row').first().waitFor({ timeout: 10000 });
  console.log('depth rows:', await depthGrid.getByRole('row').count(), '| first:', flat(await depthGrid.getByRole('row').first().textContent()));
  console.log('depth totals:', flat(await page.getByLabel('Depth totals').textContent()));

  await open('Time & Sales', 'Tape instrument');
  const tape = page.getByRole('grid', { name: 'Time and sales' });
  await page.waitForFunction(() => document.querySelectorAll('[aria-label="Time and sales"] [role=row]').length >= 3, null, { timeout: 15000 });
  console.log('tape rows:', await tape.getByRole('row').count(), '| newest:', flat(await tape.getByRole('row').first().textContent()));
  const server = await api('GET', '/api/instruments/NFO:NIFTY26OCTFUT/prints?limit=3');
  console.log('server newest print:', JSON.stringify(server.prints.at(-1)));
  const sides = await tape.locator('[role=row] span:nth-child(4)').allTextContents();
  console.log('sides seen:', [...new Set(sides)].join(' '));

  await open('Movers');
  const movers = page.getByRole('grid', { name: 'Movers' });
  await movers.getByRole('row').first().waitFor({ timeout: 10000 });
  console.log('gainers:', (await movers.getByRole('row').allTextContents()).slice(0, 3).map(flat).join(' | '));
  const top = await api('GET', '/api/market/movers?by=gainers&limit=1');
  console.log('server top gainer:', top.rows[0]?.symbol, top.rows[0]?.change_pct);
  await page.locator('.movers').getByRole('button', { name: 'Volume' }).click();
  await page.waitForTimeout(600);
  console.log('volume:', (await movers.getByRole('row').allTextContents()).slice(0, 2).map(flat).join(' | '));
  await page.screenshot({ path: '/tmp/claude-0/e2e-market.png' });

  // Alerts
  await open('Alerts');
  const alerts = page.locator('.alerts').last();
  await alerts.getByRole('button', { name: 'Webhook' }).click();
  await alerts.getByLabel('Webhook address').fill('http://hooks.example.com/x');
  await alerts.getByRole('button', { name: 'Save' }).click();
  console.log('webhook http:', flat(await alerts.getByRole('alert').textContent()));
  await alerts.getByRole('button', { name: 'New' }).click();
  const form = page.getByRole('form', { name: 'New alert' });
  await form.waitFor({ timeout: 5000 });
  const ltp = (await api('GET', '/api/instruments/NFO:NIFTY26OCTFUT')).ltp_paise;
  const level = ((ltp + 20) / 100).toFixed(2);
  if (await form.getByLabel('Alert contract').count()) {
    await form.getByLabel('Alert contract').fill('NIFTY26OCT');
    await page.getByRole('option', { name: /NIFTY26OCTFUT/ }).first().click();
  }
  await form.getByRole('textbox', { name: 'Price' }).fill(level);
  await form.getByLabel('Note').fill('e2e');
  await form.getByRole('button', { name: 'Make alert' }).click();
  const list = page.getByRole('grid', { name: 'Alerts' });
  await list.getByRole('row').first().waitFor({ timeout: 5000 });
  console.log('made:', flat(await list.getByRole('row').first().textContent()));
  await page.waitForFunction(() => /fired/.test(document.querySelector('[aria-label="Alerts"] [role=row]')?.textContent || ''), null, { timeout: 90000 });
  console.log('fired:', flat(await list.getByRole('row').first().textContent()));
  await page.locator('.dv-tab', { hasText: 'Messages' }).click();
  console.log('message:', flat(await page.getByText(/^Alert: /).first().textContent()));
  await page.locator('.dv-tab', { hasText: 'Alerts' }).click();
  await list.getByRole('row').first().getByRole('button', { name: 'On' }).click();
  await page.waitForTimeout(500);
  console.log('on again:', flat(await list.getByRole('row').first().textContent()));
  await list.getByRole('row').first().getByRole('button', { name: 'Delete…' }).click();
  await list.getByRole('row').first().getByRole('button', { name: 'Confirm delete' }).click();
  await page.waitForTimeout(500);
  console.log('after delete:', await list.getByRole('row').count(), 'rows; server:', (await api('GET', '/api/alerts')).length);
  await page.screenshot({ path: '/tmp/claude-0/e2e-alerts.png' });
  console.log('page errors:', errors.length ? errors : 'none');
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
