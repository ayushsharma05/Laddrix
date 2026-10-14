// Real browser: open the Option Chain from Widgets, watch it update, click a put to drive the
// linked ticket, switch expiry.
const { chromium } = require('/opt/node22/lib/node_modules/playwright');
const { execSync } = require('child_process');
const out = process.argv[2] || '.';
const flat = (s) => s.replace(/\s+/g, ' ').trim();
(async () => {
  const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome' });
  const page = await browser.newPage({ viewport: { width: 1680, height: 1000 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto('http://localhost:8000/');
  await page.getByLabel('Username').fill('demo');
  await page.getByLabel('Password').fill('demo-password-123');
  await page.getByLabel('Authenticator code').fill(execSync('cd /home/user/MockTrade && python3 -m mocktrade.tools.demo --code').toString().trim());
  await page.keyboard.press('Enter');
  await page.locator('.dv-tab').first().waitFor({ timeout: 20000 });
  await page.evaluate(async () => {
    for (const k of Object.keys(localStorage)) localStorage.removeItem(k);
    await fetch('/api/layouts/current', { method: 'DELETE', credentials: 'same-origin' });
  });
  await page.reload();
  await page.locator('.ladder').first().waitFor({ timeout: 20000 });
  await page.getByRole('button', { name: 'Widgets ▾' }).click();
  await page.getByRole('menuitem', { name: /Option Chain/ }).click();
  const chain = page.locator('.chain');
  await chain.locator('.chain-row.atm').waitFor({ timeout: 10000 });
  const atm = () => chain.locator('.chain-row.atm').innerText();
  const before = flat(await atm());
  console.log('ATM row :', before);
  await page.waitForTimeout(2500);
  console.log('ATM row later:', flat(await atm()));
  console.log('rows:', await chain.locator('.grid-body .chain-row').count(), '| footer:', flat(await chain.locator('.chain-foot').innerText()));
  console.log('chain box before:', JSON.stringify(await chain.boundingBox()));
  await page.locator('.tab', { hasText: 'Chain' }).dblclick();  // maximize
  await page.waitForTimeout(400);
  console.log('chain box maximized:', JSON.stringify(await chain.boundingBox()));
  // the default layout's ticket is on the pink link: clicking a put switches it
  await chain.getByRole('button', { name: 'Put 25,100' }).click();
  await page.waitForTimeout(800);
  console.log('ticket now:', flat(await page.locator('.ticket-head').first().innerText()).slice(0, 80));
  await page.screenshot({ path: `${out}/14-chain.png` });
  await page.locator('.tab', { hasText: 'Chain' }).dblclick();  // restore
  await page.waitForTimeout(400);
  console.log('ticket now (restored):', flat(await page.locator('.ticket-head').first().innerText()).slice(0, 80));
  await page.locator('.tab', { hasText: 'Chain' }).dblclick();
  await chain.getByRole('button', { name: '24 Nov' }).click();
  await page.waitForTimeout(1500);
  console.log('Nov ATM row:', flat(await atm()), '| strikes shown:', await chain.locator('.grid-body .chain-row').count());
  console.log('errors:', JSON.stringify(errors));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
