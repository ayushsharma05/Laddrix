// Kite mode before the daily login: the strip says so, orders are gated, and the button
// leads to Zerodha's login page with our API key and a one-time state.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { execSync } = require('child_process');
const secret = process.argv[2];
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto('http://localhost:8000/');
  await page.getByLabel('Username').fill('ayush');
  await page.getByLabel('Password').fill('compose-test-password-1');
  const code = execSync(`PYTHONPATH=src python3 -c "import time;from mocktrade.server.security import totp_code;print(totp_code('${secret}', time.time()))"`).toString().trim();
  await page.getByLabel('Authenticator code').fill(code);
  await page.keyboard.press('Enter');
  await page.locator('.env-strip').waitFor({ timeout: 20000 });
  await page.getByLabel('Account for new orders').selectOption('acc-kite');
  console.log('strip:', await page.locator('.env-strip').innerText());
  await page.waitForTimeout(1500);
  await page.screenshot({ path: '/tmp/claude-0/-home-user-MockTrade/d1180695-f459-5b78-b02f-e0982b23c5c6/scratchpad/e2e/13-kite-login-needed.png' });
  await page.route('https://kite.zerodha.com/**', (route) => route.fulfill({ status: 200, body: 'zerodha login page (stub)' }));
  const nav = page.waitForRequest((r) => r.url().startsWith('https://kite.zerodha.com/'));
  await page.getByRole('button', { name: 'Log in to Kite' }).click();
  const url = new URL((await nav).url());
  console.log('went to:', url.origin + url.pathname, 'api_key=' + url.searchParams.get('api_key'), 'v=' + url.searchParams.get('v'), 'state present=' + !!new URLSearchParams(url.searchParams.get('redirect_params') || '').get('state'));
  console.log('errors:', JSON.stringify(errors));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
