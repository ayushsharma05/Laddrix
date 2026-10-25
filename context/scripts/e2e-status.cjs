// Real-browser check of the status page (#287) on the demo: open the Admin widget from the
// right-click menu, its Status tab lists each part of the server as GET /api/status says
// it (the same rows, the same states); every answer names its request (X-Request-ID);
// /api/ready agrees with the page; no page errors.
//
//   python3 -m laddrix.tools.demo --port 8000 --ui ui/dist
//   node context/scripts/e2e-status.cjs <folder for screenshots>
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
  page.on('console', (m) => { if (m.type() === 'error' && !/401|404|409|422/.test(m.text())) errors.push(m.text()); });

  await page.goto('http://localhost:8000/');
  await page.getByLabel('Username').fill('demo');
  await page.getByLabel('Password').fill('demo-password-123');
  await page.getByLabel('Authenticator code').fill(execFileSync('python3', ['-m', 'laddrix.tools.demo', '--code']).toString().trim());
  await page.keyboard.press('Enter');
  await page.locator('.frame .ladder .price.last').first().waitFor({ timeout: 20000 });

  const answer = await page.evaluate(async () => {
    const r = await fetch('/api/status');
    const ready = await fetch('/api/ready');
    return { id: r.headers.get('x-request-id'), body: await r.json(), ready: await ready.json() };
  });
  check('every answer names its request', /^[0-9a-f]{16}$/.test(answer.id || ''), answer.id);
  check('readiness agrees with the status page', answer.ready.ready === answer.body.ready, JSON.stringify(answer.ready));

  await page.mouse.click(1560, 880, { button: 'right' });
  await page.locator('.context-menu').getByRole('menuitem', { name: /Open widget/ }).hover();
  await page.getByRole('menuitem', { name: /^Admin/ }).click();
  const admin = page.locator('section.frame:has(.admin)').last();
  await admin.getByRole('button', { name: 'Status' }).click();
  const header = admin.getByRole('status', { name: 'Server status' });
  await header.waitFor({ timeout: 5000 });
  await admin.evaluate((el) => el.scrollIntoView());
  check('the header says the worst state', (await header.innerText()).startsWith(answer.body.state), (await header.innerText()).split('\n')[0]);
  const rows = await admin.locator('.admin-status-row').evaluateAll((els) => els.map((e) => e.getAttribute('aria-label')));
  const names = answer.body.parts.map((p) => p.name);
  check('one row for each part the server names, in its order', JSON.stringify(rows) === JSON.stringify(names), `${rows.length} parts`);
  const server = admin.getByLabel('server', { exact: true });
  const cells = await server.locator('span').allInnerTexts();
  check('a part says its state, why and since when', cells.length === 4 && cells.slice(0, 3).join('|') === 'server|ready|answering' && /^since \d\d:\d\d:\d\d$/.test(cells[3]), cells.join(' | '));
  await page.waitForTimeout(500);
  await admin.screenshot({ path: `${out}/status-page.png` });
  check('no errors in the page', errors.length === 0, JSON.stringify(errors));
  await browser.close();
  const passed = results.filter(Boolean).length;
  console.log(`\n${passed}/${results.length} passed`);
  process.exit(passed === results.length ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
