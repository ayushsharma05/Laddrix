const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { execSync } = require('child_process');
const out = process.argv[2];
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined }).catch(() => chromium.launch());
  const page = await browser.newPage({ viewport: { width: 1600, height: 950 } });
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.goto('http://localhost:8000/');
  await page.getByLabel('Username').fill('demo');
  await page.getByLabel('Password').fill('demo-password-123');
  const code = execSync('python3 -m laddrix.tools.demo --code').toString().trim();
  await page.getByLabel('Authenticator code').fill(code);
  await page.keyboard.press('Enter');
  const ladder = page.locator('.ladder').first();
  await ladder.waitFor({ timeout: 20000 });
  await page.locator('.ladder .price.last').first().waitFor({ timeout: 20000 });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${out}/1-workspace.png` });
  const status = () => ladder.locator('.ladder-status .text').innerText();

  // 1) a resting buy two rows under the best bid
  const bestBid = ladder.locator('.lc.bid.best').first();
  const bidRow = bestBid.locator('xpath=..');
  const bidPrice = Number(await bidRow.getAttribute('data-price'));
  const tick = Number(await ladder.locator('.ladder-row[data-price]').nth(0).getAttribute('data-price')) - Number(await ladder.locator('.ladder-row[data-price]').nth(1).getAttribute('data-price'));
  const restPrice = bidPrice - 2 * tick;
  await ladder.locator(`.ladder-row[data-price="${restPrice}"] .lc.bid`).click();
  await page.waitForTimeout(800);
  console.log('after resting buy:', await status());
  console.log('work cell:', await ladder.locator(`.ladder-row[data-price="${restPrice}"] .lc.work`).innerText(), 'piq:', await ladder.locator(`.ladder-row[data-price="${restPrice}"] .lc.piq`).innerText());

  // 2) move it one row lower: select, then another row's Work
  await ladder.locator(`.ladder-row[data-price="${restPrice}"] .lc.work`).click();
  await ladder.locator(`.ladder-row[data-price="${restPrice - tick}"] .lc.work`).click();
  await page.waitForTimeout(800);
  console.log('after move:', await status(), '| work at new price:', await ladder.locator(`.ladder-row[data-price="${restPrice - tick}"] .lc.work`).innerText());
  await page.screenshot({ path: `${out}/2-working.png` });

  // 3) cancel it: select, click again
  await ladder.locator(`.ladder-row[data-price="${restPrice - tick}"] .lc.work`).click();
  await ladder.locator(`.ladder-row[data-price="${restPrice - tick}"] .lc.work`).click();
  await page.waitForTimeout(800);
  console.log('after cancel:', await status());

  // 4) a marketable buy: Bids column at the best ask price -> fills
  const askRow = ladder.locator('.lc.ask.best').first().locator('xpath=..');
  const askPrice = Number(await askRow.getAttribute('data-price'));
  await ladder.locator(`.ladder-row[data-price="${askPrice}"] .lc.bid`).click();
  await page.waitForTimeout(2500);
  console.log('after marketable buy:', await status());
  console.log('position:', await ladder.locator('.ladder-pos').innerText().then((t) => t.replace(/\n/g, ' ')));
  await page.screenshot({ path: `${out}/3-filled.png` });
  console.log('errors:', JSON.stringify(errors));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
