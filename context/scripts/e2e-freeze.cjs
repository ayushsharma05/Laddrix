// Real-browser check of freeze quantities (#424) on the demo (sample limits: NIFTY 1,800):
// the ticket says "max 1,800 per order", refuses 25 lots before the click in words, takes
// 24; the order spec and the instrument carry the limit; no page errors.
//
//   python3 -m mocktrade.tools.demo --port 8000 --ui ui/dist      (saved layouts cleared)
//   node context/scripts/e2e-freeze.cjs <folder for screenshots>
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { execFileSync } = require('child_process');
const out = process.argv[2] || '.';
const results = [];
const check = (name, ok, detail = '') => {
  results.push(ok);
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`);
};

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
  const page = await (await browser.newContext({ viewport: { width: 1600, height: 950 } })).newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => { if (m.type() === 'error' && !/401|404|422/.test(m.text())) errors.push(m.text()); });

  await page.goto('http://localhost:8000/');
  await page.getByLabel('Username').fill('demo');
  await page.getByLabel('Password').fill('demo-password-123');
  await page.getByLabel('Authenticator code').fill(execFileSync('python3', ['-m', 'mocktrade.tools.demo', '--code']).toString().trim());
  await page.keyboard.press('Enter');
  const ticket = page.locator('section.frame:has(.ticket-head)').first();
  await ticket.waitFor({ timeout: 20000 });
  await ticket.evaluate((el) => el.scrollIntoView());
  const head = ticket.locator('.ticket-head');
  await page.waitForFunction(() => /max 1,800 per order/.test(document.querySelector('.ticket-head')?.textContent ?? ''), null, { timeout: 10000 });
  check('the ticket says the most per order', (await head.innerText()).includes('max 1,800 per order'), (await head.innerText()).split('\n')[1]);

  const lots = ticket.getByLabel('Lots');
  await lots.fill('25');
  const send = ticket.locator('button.ticket-send');
  await page.waitForTimeout(300);
  const title = await send.getAttribute('title');
  check('25 lots: refused before the click, in words', (await send.isDisabled()) && /the exchange takes at most 1800 \(24 lots\) in one order/.test(title ?? ''), title ?? '');
  await page.screenshot({ path: `${out}/freeze-ticket.png` });
  await lots.fill('24');
  await page.waitForTimeout(300);
  check('24 lots: allowed', !(await send.isDisabled()), await send.getAttribute('title'));

  const facts = await page.evaluate(async () => {
    const id = encodeURIComponent('NFO:NIFTY' + document.querySelector('.ticket-head b').textContent.slice(5));
    const me = await (await fetch('/api/me')).json();
    const spec = await (await fetch(`/api/accounts/${me.accounts[0].id}/instruments/${id}/order-spec`)).json();
    const instrument = await (await fetch(`/api/instruments/${id}`)).json();
    return { spec: spec.freeze_qty, instrument: instrument.freeze_qty };
  });
  check('the order spec and the instrument carry it', facts.spec === 1800 && facts.instrument === 1800, JSON.stringify(facts));
  check('no page errors', errors.length === 0, errors.join(' | '));
  await browser.close();
  const passed = results.filter(Boolean).length;
  console.log(`${passed}/${results.length} checks passed`);
  process.exit(passed === results.length ? 0 : 1);
})();
