// Real browser through Caddy (https://localhost): log in, trade from the ladder over the
// proxied WebSocket, check the fill arrives live.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { execSync } = require('child_process');
const secret = process.argv[2];
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--no-proxy-server'] });
  const page = await browser.newPage({ viewport: { width: 1600, height: 950 }, ignoreHTTPSErrors: true });
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto('https://localhost/');
  await page.getByLabel('Username').fill('ayush');
  await page.getByLabel('Password').fill('compose-test-password-1');
  const code = execSync(`PYTHONPATH=src python3 -c "import time;from mocktrade.server.security import totp_code;print(totp_code('${secret}', time.time()))"`).toString().trim();
  await page.getByLabel('Authenticator code').fill(code);
  await page.keyboard.press('Enter');
  const ladder = page.locator('.ladder').first();
  await ladder.waitFor({ timeout: 20000 });
  await ladder.locator('.lc.ask.best').first().waitFor({ timeout: 15000 });
  console.log('top:', (await page.locator('body').innerText()).replace(/\s+/g, ' ').slice(0, 140));
  // buy at the best ask: marketable, fills on paper
  const t0 = Date.now();
  const askPrice = await ladder.locator('.lc.ask.best').first().evaluate((el) => el.getAttribute('aria-label').replace('Sell at ', ''));
  await ladder.getByRole('button', { name: `Buy at ${askPrice}`, exact: true }).click();
  await page.waitForFunction(() => /Bought/.test(document.body.innerText), null, { timeout: 10000 }).catch(() => {});
  console.log('fill seen in', Date.now() - t0, 'ms');
  await page.waitForTimeout(3000);
  console.log('positions:', (await page.getByRole('grid', { name: 'Positions' }).innerText()).replace(/\s+/g, ' ').slice(0, 120));
  await page.screenshot({ path: '/tmp/claude-0/-home-user-MockTrade/d1180695-f459-5b78-b02f-e0982b23c5c6/scratchpad/e2e/12-compose.png' });
  console.log('audit rows:', await page.getByRole('grid', { name: /audit/i }).getByRole('row').count().catch(() => 'n/a'));
  console.log('errors:', JSON.stringify(errors));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
