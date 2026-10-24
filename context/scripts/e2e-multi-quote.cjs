// Real-browser check of several quoting legs (#427) on the demo: tick both legs of a
// calendar to quote in the Autospreader form, see it kept, sell 2 spread lots above the market
// (each leg quotes 1 lot, its role "quote"), see both quotes in the Algos screen's details, stop.
//
//   python3 -m mocktrade.tools.demo --port 8000 --ui ui/dist      (saved layouts and
//   data/demo/spreads cleared)
//   node context/scripts/e2e-multi-quote.cjs <folder for screenshots>
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
  await page.getByLabel('Authenticator code').fill(execFileSync('python3', ['-m', 'mocktrade.tools.demo', '--code']).toString().trim());
  await page.keyboard.press('Enter');
  await page.locator('.frame .ladder .price.last').first().waitFor({ timeout: 20000 });
  const api = (path, init) => page.evaluate(async ([p, i]) => {
    const r = await fetch(p, i ? { method: i.method, headers: { 'content-type': 'application/json' }, body: JSON.stringify(i.body) } : undefined);
    return r.status === 204 ? null : r.json();
  }, [path, init]);

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
  await ase.getByLabel('Spread name').fill('Both legs');
  await ase.getByLabel('Quote leg 1').click(); // the only quoting leg: it stays ticked
  check('the last quoting leg can\'t be unticked', await ase.getByLabel('Quote leg 1').isChecked());
  await ase.getByLabel('Quote leg 2').click(); // leg 2 quotes too
  await page.screenshot({ path: `${out}/multi-quote-form.png` });
  await ase.getByRole('button', { name: 'Create spread' }).click();
  await ase.getByText('Both legs').waitFor({ timeout: 5000 });
  const spread = (await api('/api/spreads')).find((s) => s.name === 'Both legs');
  check('both legs quote', spread?.legs.every((l) => l.quote), JSON.stringify(spread?.legs.map((l) => l.quote)));

  const algo = await api(`/api/accounts/acc-paper/spreads/${spread.spread_id}/orders`, { method: 'POST', body: { side: 'SELL', lots: 2, price_paise: -13000 } });
  let quotes = [];
  for (let i = 0; i < 40 && quotes.length < 2; i++) {
    await page.waitForTimeout(250);
    const orders = await api('/api/accounts/acc-paper/orders');
    quotes = orders.filter((o) => o.request.parent_id === algo.algo_id && o.request.algo_role === 'quote' && ['OPEN', 'PENDING'].includes(o.status));
  }
  const seen = quotes.map((o) => `${o.request.side} ${o.request.qty} ${o.request.instrument_id.slice(4)}`).sort();
  check('selling 2 lots: each leg quotes 1 lot', seen.join(' | ') === 'BUY 75 NIFTY26NOVFUT | SELL 75 NIFTY26OCTFUT', seen.join(' | '));

  await page.mouse.click(1560, 880, { button: 'right' });
  await page.locator('.context-menu').getByRole('menuitem', { name: /Open widget/ }).hover();
  await page.getByRole('menuitem', { name: /^Algos/ }).click();
  const algos = page.locator('section.frame:has(.algos)');
  await algos.waitFor({ timeout: 5000 });
  await algos.evaluate((el) => el.scrollIntoView());
  const row = algos.locator(`[title^="${algo.algo_id}"]`);
  await row.dblclick();
  const detail = algos.getByRole('region', { name: `${algo.algo_id} details` });
  await detail.waitFor({ timeout: 5000 });
  const text = await detail.innerText();
  check('the algo screen shows both quotes', text.includes('NIFTY26OCTFUT') && text.includes('NIFTY26NOVFUT'), text.split('\n').slice(-2).join(' / '));
  await page.screenshot({ path: `${out}/multi-quote-algos.png` });
  await row.click();
  await algos.getByRole('button', { name: 'Stop', exact: true }).click();
  let state;
  for (let i = 0; i < 40 && state !== 'STOPPED'; i++) {
    await page.waitForTimeout(250);
    state = (await api('/api/accounts/acc-paper/algos')).find((a) => a.algo_id === algo.algo_id)?.state;
  }
  check('Stop pulls both quotes and ends it', state === 'STOPPED', state);
  check('no errors in the page', errors.length === 0, JSON.stringify(errors));
  await browser.close();
  const passed = results.filter(Boolean).length;
  console.log(`\n${passed}/${results.length} passed`);
  process.exit(passed === results.length ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
