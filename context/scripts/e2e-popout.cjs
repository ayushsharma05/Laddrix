// Real-browser check of popping a widget out into its own window and docking it back.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { execSync } = require('child_process');
const out = process.argv[2] || '.';
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
  const context = await browser.newContext({ viewport: { width: 1600, height: 950 } });
  const page = await context.newPage();
  const errors = [];
  const watch = (p) => {
    p.on('console', (m) => { if (m.type() === 'error' && !/401/.test(m.text())) errors.push(m.text()); });
    p.on('pageerror', (e) => errors.push(String(e)));
  };
  watch(page);
  context.on('page', watch);
  await page.goto('http://localhost:8000/');
  await page.getByLabel('Username').fill('demo');
  await page.getByLabel('Password').fill('demo-password-123');
  await page.getByLabel('Authenticator code').fill(execSync('python3 -m mocktrade.tools.demo --code').toString().trim());
  await page.keyboard.press('Enter');
  await page.locator('.ladder .price.last').first().waitFor({ timeout: 20000 });
  const ladders = await page.locator('.ladder').count();

  // pop the first ladder out
  const [popup] = await Promise.all([
    context.waitForEvent('page'),
    page.getByRole('button', { name: 'Pop out' }).nth(1).click(), // 0 = watch list, 1 = first ladder
  ]);
  await popup.waitForLoadState();
  await popup.locator('.ladder .price.last').waitFor({ timeout: 10000 });
  const before = await popup.locator('.ladder .price.last').innerText();
  let after = before;
  for (let i = 0; i < 20 && after === before; i++) {
    await popup.waitForTimeout(500);
    after = await popup.locator('.ladder .price.last').innerText();
  }
  console.log('ladders in main before:', ladders, '| after pop-out:', await page.locator('.ladder').count());
  console.log('popped ladder live:', before, '->', after, after !== before ? 'UPDATED' : 'no change in 10 s');
  // trade from the popped window: same session, same store
  await popup.locator('.lc.bid.best').first().click();
  await popup.waitForTimeout(1500);
  console.log('popped window status:', await popup.locator('.ladder-status .text').innerText());
  await popup.setViewportSize({ width: 520, height: 800 });
  await popup.screenshot({ path: `${out}/7-popout.png` });
  // dock it back
  await popup.getByRole('button', { name: 'Dock back' }).click();
  await page.waitForTimeout(1000);
  console.log('after dock back, ladders in main:', await page.locator('.ladder').count(), '| popup closed:', popup.isClosed());
  console.log('errors:', JSON.stringify(errors));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
