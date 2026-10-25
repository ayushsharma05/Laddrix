// Real browser check of #264 on the demo: run after a demo had an MIS position with a trailing
// stop and was restarted (see context/scripts/README.md). The UNPROTECTED band shows; Restore
// stop needs two clicks; the stop runs again and the band clears.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
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
  const band = page.locator('.band', { hasText: 'UNPROTECTED' });
  await band.waitFor({ timeout: 20000 });
  console.log('band:', (await band.innerText()).replace(/\s+/g, ' ').slice(0, 260));
  await page.screenshot({ path: process.env.SHOT || '/tmp/restore-band.png', clip: { x: 0, y: 0, width: 1600, height: 140 } });
  const button = band.getByRole('button', { name: 'Restore stop' });
  if (!(await button.count())) { console.log('no button (blocked)'); await browser.close(); process.exit(1); }
  await button.click();
  console.log('after one click:', await band.getByRole('button').innerText());
  await band.getByRole('button').click();
  await page.waitForTimeout(7000); // the guard checks every 5 s
  const algos = await page.evaluate(async () => (await (await fetch('/api/accounts/acc-paper/algos')).json()).map((a) => `${a.algo_id} ${a.code} ${a.qty} ${a.state}`));
  const still = await page.locator('.band', { hasText: 'UNPROTECTED' }).count();
  console.log('algos now:', algos.join(' | '), '| band still showing:', still > 0);
  const ok = algos.some((a) => a.includes('TRAILING_STOP') && a.includes('WORKING')) && still === 0;
  console.log(ok ? 'PASS' : 'FAIL');
  await browser.close();
  process.exit(ok ? 0 : 1);
})();
