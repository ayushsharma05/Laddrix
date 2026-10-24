// Real-browser check of the Autospreader's controls and actions (#425) on the demo: set a
// calendar's controls in the form (drawn from the server's list), see them kept, trade it
// so the quote rests (max unhedged 1: a 3-lot order quotes 1 lot), open the algo screen,
// preview Flatten and Hedge now (the server says why not, in words), and stop it.
//
//   python3 -m mocktrade.tools.demo --port 8000 --ui ui/dist      (saved layouts and
//   data/demo/spreads cleared)
//   node context/scripts/e2e-ase-controls.cjs <folder for screenshots>
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
  const expected = /\/api\/(layouts\/current|replay)$/;
  page.on('console', (m) => { if (m.type() === 'error' && !/401|404|409|422/.test(m.text())) errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('response', (r) => { if (r.status() === 404 && !expected.test(new URL(r.url()).pathname)) errors.push(`404 ${r.url()}`); });

  await page.goto('http://localhost:8000/');
  await page.getByLabel('Username').fill('demo');
  await page.getByLabel('Password').fill('demo-password-123');
  await page.getByLabel('Authenticator code').fill(execFileSync('python3', ['-m', 'mocktrade.tools.demo', '--code']).toString().trim());
  await page.keyboard.press('Enter');
  await page.locator('.frame .ladder .price.last').first().waitFor({ timeout: 20000 });
  const api = (path, init) => page.evaluate(async ([p, i]) => {
    const r = await fetch(p, i ? { method: i.method, headers: { 'content-type': 'application/json' }, body: JSON.stringify(i.body) } : undefined);
    return r.status === 204 ? null : r.json();
  }, [path, init]);

  // the form: legs, name and the controls drawn from the server's list
  await page.mouse.click(1560, 880, { button: 'right' });
  await page.locator('.context-menu').getByRole('menuitem', { name: /Open widget/ }).hover();
  await page.getByRole('menuitem', { name: /^Autospreader/ }).click();
  const ase = page.locator('section.frame:has(.ase)');
  await ase.waitFor({ timeout: 5000 });
  await ase.evaluate((el) => el.scrollIntoView());
  await ase.getByRole('button', { name: 'New', exact: true }).click();
  await ase.getByLabel('Leg 1 contract').fill('NIFTY26OCT');
  await page.getByRole('option', { name: /NIFTY26OCTFUT/ }).first().dispatchEvent('mousedown');
  await ase.getByLabel('Leg 2 contract').fill('NIFTY26NOV');
  await page.getByRole('option', { name: /NIFTY26NOVFUT/ }).first().dispatchEvent('mousedown');
  await ase.getByLabel('Spread name').fill('NIFTY Oct/Nov');
  const labels = await ase.locator('.ase-controls .algo-field > span:first-child').allInnerTexts();
  check('the controls come from the server\'s list', labels.join('|').startsWith('Re-quote threshold'), labels.join(' | '));
  await ase.getByLabel('Max unhedged').fill('0');
  await ase.getByLabel('Re-quote threshold').fill('0');
  await ase.getByRole('button', { name: 'Create spread' }).click();
  check('a bad control is said in words before sending', (await ase.getByRole('alert').innerText()).includes('Re-quote threshold must be at least 1'));
  await ase.getByLabel('Re-quote threshold').fill('2');
  await ase.getByLabel('Max unhedged').fill('1');
  await ase.getByLabel('Hedge timeout').fill('3');
  await page.screenshot({ path: `${out}/ase-controls-form.png` });
  await ase.getByRole('button', { name: 'Create spread' }).click();
  await ase.getByText('NIFTY Oct/Nov').waitFor({ timeout: 5000 });
  const spread = (await api('/api/spreads')).find((s) => s.name === 'NIFTY Oct/Nov');
  check('the spread keeps its controls', spread?.controls?.requote_ticks === 2 && spread.controls.max_unhedged_lots === 1 && spread.controls.hedge_timeout_s === 3, JSON.stringify(spread?.controls));

  // sell 3 spread lots above the market: the quote rests, 1 lot at a time (selling also
  // needs no new margin on a demo account that already holds calendars)
  const algo = await api(`/api/accounts/acc-paper/spreads/${spread.spread_id}/orders`, { method: 'POST', body: { side: 'SELL', lots: 3, price_paise: -13000 } });
  let quote;
  for (let i = 0; i < 40 && !quote; i++) {
    await page.waitForTimeout(250);
    const orders = await api('/api/accounts/acc-paper/orders');
    quote = orders.find((o) => o.request.parent_id === algo.algo_id && !['CANCELLED', 'REJECTED', 'FILLED'].includes(o.status));
  }
  const seen = (await api('/api/accounts/acc-paper/algos')).find((a) => a.algo_id === algo.algo_id);
  check('max unhedged 1: a 3-lot spread order quotes 1 lot', quote?.request.qty === 75, `${quote?.request.side} ${quote?.request.qty} @ ${quote?.request.price_paise}; ${seen?.state} ${seen?.status} ${seen?.reason}`);

  // the algo screen: its actions, previewed in the server's words
  await page.mouse.click(1560, 880, { button: 'right' });
  await page.locator('.context-menu').getByRole('menuitem', { name: /Open widget/ }).hover();
  await page.getByRole('menuitem', { name: /^Algos/ }).click();
  const algos = page.locator('section.frame:has(.algos)');
  await algos.waitFor({ timeout: 5000 });
  await algos.evaluate((el) => el.scrollIntoView());
  await algos.locator(`[title^="${algo.algo_id}"]`).click();
  await algos.getByRole('button', { name: 'Flatten…' }).click();
  const flatten = page.getByRole('dialog', { name: `Flatten ${algo.algo_id}` });
  await flatten.waitFor({ timeout: 5000 });
  check('Flatten says why not, with nothing to confirm', (await flatten.innerText()).includes('nothing is unhedged: use Stop') && (await flatten.getByRole('button', { name: 'Confirm' }).count()) === 0);
  await page.screenshot({ path: `${out}/ase-controls-actions.png` });
  await flatten.getByRole('button', { name: 'Close' }).click();
  await algos.getByRole('button', { name: 'Hedge now…' }).click();
  const hedge = page.getByRole('dialog', { name: `Hedge now ${algo.algo_id}` });
  check('Hedge now: nothing is unhedged', (await hedge.innerText()).includes('nothing is unhedged'));
  await hedge.getByRole('button', { name: 'Close' }).click();
  await algos.getByRole('button', { name: 'Stop', exact: true }).click();
  let state;
  for (let i = 0; i < 40 && state !== 'STOPPED'; i++) {
    await page.waitForTimeout(250);
    state = (await api('/api/accounts/acc-paper/algos')).find((a) => a.algo_id === algo.algo_id)?.state;
  }
  check('Stop ends it', state === 'STOPPED', state);
  check('no errors in the page', errors.length === 0, JSON.stringify(errors));
  await browser.close();
  const passed = results.filter(Boolean).length;
  console.log(`\n${passed}/${results.length} passed`);
  process.exit(passed === results.length ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
