// Real-browser check of the order ticket against the demo server (paper broker):
// LMT, MKT, SL and SL-M from the ticket, then the order book's counts.
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
  await page.locator('.ladder').first().waitFor({ timeout: 20000 });
  // start from the default workspace (it has a ticket): forget any saved layout, then reload
  await page.evaluate(async () => {
    for (const k of Object.keys(localStorage)) localStorage.removeItem(k);
    await fetch('/api/layouts/current', { method: 'DELETE', credentials: 'same-origin' });
  });
  await page.reload();
  const ticket = page.locator('.ticket').first();
  await ticket.waitFor({ timeout: 20000 });
  const price = ticket.getByLabel('Price', { exact: true });
  await page.waitForFunction(() => document.querySelector('.ticket input[name=price]')?.value !== '');
  const status = () => ticket.locator('.ladder-status .text').innerText();
  const ltpText = await ticket.locator('.ticket-head b').nth(1).innerText();
  const ltp = Number(ltpText.replace(/,/g, ''));
  const type = (t) => ticket.getByRole('button', { name: t, exact: true }).click();
  const sendBtn = () => ticket.locator('.ticket-send');

  // 1) LMT buy 5 ticks under the last price: rests
  await type('LMT');
  for (let i = 0; i < 5; i++) await ticket.getByLabel('Price one tick lower').click();
  await sendBtn().click();
  await page.waitForTimeout(800);
  console.log('LMT  :', await status());
  // 2) MKT buy: fills
  await type('MKT');
  console.log('MKT summary:', flat(await ticket.getByLabel('Order summary').innerText()));
  await sendBtn().click();
  await page.waitForTimeout(1500);
  console.log('MKT  :', await status());
  // 3) SL-M sell with the trigger 20 rupees under the last price: waits for its trigger
  await ticket.getByRole('button', { name: 'SELL', exact: true }).click();
  await type('SL-M');
  await ticket.getByLabel('Trigger', { exact: true }).fill(String(ltp - 20));
  await sendBtn().click();
  await page.waitForTimeout(800);
  console.log('SL-M :', await status());
  // 4) SL buy: a trigger below the market is refused before sending
  await ticket.getByRole('button', { name: 'BUY', exact: true }).click();
  await type('SL');
  await price.fill(String(ltp + 30));
  await ticket.getByLabel('Trigger', { exact: true }).fill(String(ltp - 10));
  console.log('SL bad trigger:', await status(), '| send disabled:', await sendBtn().isDisabled());
  await ticket.getByLabel('Trigger', { exact: true }).fill(String(ltp + 20));
  await sendBtn().click();
  await page.waitForTimeout(800);
  console.log('SL   :', await status());
  await page.waitForTimeout(1500);
  console.log('order book:', (await page.locator('.book-tab').allInnerTexts()).slice(0, 4).join(' | '));
  console.log('working:', (await page.getByRole('grid', { name: 'Orders' }).getByRole('row').allInnerTexts()).map(flat).join(' / '));
  await page.screenshot({ path: `${out}/6-ticket.png` });
  console.log('errors:', JSON.stringify(errors));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
