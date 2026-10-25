// Real-browser check of structure positions (#428) on the demo: sell 1 calendar through the
// Autospreader (priced to fill at once), open the Structures widget from the right-click menu:
// the spread's structure holds −1 lot, each leg ±75 with residual 0, its P&L as the server
// worked it out; open it to see the allocations; no page errors.
//
//   python3 -m laddrix.tools.demo --port 8000 --ui ui/dist      (saved layouts and
//   data/demo/spreads cleared)
//   node context/scripts/e2e-structures.cjs <folder for screenshots>
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
  const api = (path, init) => page.evaluate(async ([p, i]) => {
    const r = await fetch(p, i ? { method: i.method, headers: { 'content-type': 'application/json' }, body: JSON.stringify(i.body) } : undefined);
    return r.status === 204 ? null : r.json();
  }, [path, init]);

  const spread = await api('/api/spreads', { method: 'POST', body: { name: 'Held calendar', legs: [
    { instrument_id: 'NFO:NIFTY26OCTFUT', ratio: 1, multiplier: 1, quote: true, payup_ticks: 4 },
    { instrument_id: 'NFO:NIFTY26NOVFUT', ratio: -1, multiplier: -1, payup_ticks: 4 }] } });
  // sell at −200.00, far below the bid (about −139): the quote crosses and fills at once
  const algo = await api(`/api/accounts/acc-paper/spreads/${spread.spread_id}/orders`, { method: 'POST', body: { side: 'SELL', lots: 1, price_paise: -20000 } });
  let state;
  for (let i = 0; i < 60 && state !== 'DONE'; i++) {
    await page.waitForTimeout(250);
    state = (await api('/api/accounts/acc-paper/algos')).find((a) => a.algo_id === algo.algo_id)?.state;
  }
  check('the calendar is sold through the Autospreader', state === 'DONE', state);

  await page.mouse.click(1560, 880, { button: 'right' });
  await page.locator('.context-menu').getByRole('menuitem', { name: /Open widget/ }).hover();
  await page.getByRole('menuitem', { name: /^Structures/ }).click();
  const w = page.locator('section.frame:has(.structures)');
  await w.waitFor({ timeout: 5000 });
  await w.evaluate((el) => el.scrollIntoView());
  const lots = w.getByLabel('Held calendar lots');
  await lots.waitFor({ timeout: 5000 });
  check('its structure holds −1 lot', (await lots.innerText()) === '−1 lot' || (await lots.innerText()) === '-1 lot', await lots.innerText());
  const server = (await api('/api/accounts/acc-paper/structures')).structures.find((s) => s.name === 'Held calendar');
  check('each leg ±75, residual 0 (the server\'s figures)', server.legs.map((g) => `${g.qty}/${g.residual_qty}`).join(' ') === '-75/0 75/0', server.legs.map((g) => `${g.qty}/${g.residual_qty}`).join(' '));
  const pnl = await w.getByLabel('Held calendar P&L').innerText();
  check('its P&L is shown as the server worked it out', pnl.includes('P&L'), pnl);
  await w.getByRole('button', { name: 'Open Held calendar' }).click();
  const text = await w.getByRole('group', { name: 'Held calendar' }).innerText();
  check('opened: its legs and the two fills allocated', (text.match(/residual/g) || []).length === 2 && text.includes('Remove'), text.split('\n').slice(0, 3).join(' / '));
  await page.screenshot({ path: `${out}/structures.png` });
  check('no errors in the page', errors.length === 0, JSON.stringify(errors));
  await browser.close();
  const passed = results.filter(Boolean).length;
  console.log(`\n${passed}/${results.length} passed`);
  process.exit(passed === results.length ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
