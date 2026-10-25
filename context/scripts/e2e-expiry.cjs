// #290 in a real browser on the demo with its near contracts expiring today
// (LADDRIX_SIM_NEAR_EXPIRY): the default screen after login, where the ticket, the positions
// and the watch list show the server's expiry warning. Saves a screenshot (argument 1).
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { execSync } = require('child_process');
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  await page.goto('http://localhost:8000/');
  await page.getByLabel('Username').fill('demo');
  await page.getByLabel('Password').fill('demo-password-123');
  await page.getByLabel('Authenticator code').fill(execSync('python3 -m laddrix.tools.demo --code').toString().trim());
  await page.keyboard.press('Enter');
  await page.locator('section.frame').first().waitFor({ timeout: 20000 });
  await page.waitForTimeout(3000);
  await page.getByRole('row', { name: /NIFTY26OCTFUT/ }).first().click(); // the ticket follows (link)
  await page.waitForTimeout(2000);
  const tags = await page.locator('.expiry-tag').allTextContents();
  const notes = await page.locator('.expiry-note').allTextContents();
  console.log('tags:', JSON.stringify(tags));
  console.log('notes:', JSON.stringify(notes));
  await page.screenshot({ path: process.argv[2] || 'expiry.png' });
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
