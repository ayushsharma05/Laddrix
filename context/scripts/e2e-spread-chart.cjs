// Real-browser check of a spread's chart (#429) on the demo: define a calendar, open its chart
// from the Autospreader's Chart button; the chart says it is a spread mark from the legs, and
// its candles (from the server) equal the legs' own candles together, bucket by bucket.
//
//   python3 -m mocktrade.tools.demo --port 8000 --ui ui/dist      (saved layouts and
//   data/demo/spreads cleared)
//   node context/scripts/e2e-spread-chart.cjs <folder for screenshots>
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
  const OCT = 'NFO:NIFTY26OCTFUT';
  const NOV = 'NFO:NIFTY26NOVFUT';
  const name = `Charted ${Date.now() % 100000}`; // a run's own: an earlier run's may be there
  const spread = await api('/api/spreads', { method: 'POST', body: { name, legs: [
    { instrument_id: OCT, ratio: 1, multiplier: 1, quote: true }, { instrument_id: NOV, ratio: -1, multiplier: -1 }] } });

  const ids = () => page.locator('section.frame:has(.ase)').evaluateAll((els) => els.map((f) => f.dataset.widgetId));
  const before = await ids(); // the demo's layout may have an Autospreader already
  await page.mouse.click(1560, 880, { button: 'right' });
  await page.locator('.context-menu').getByRole('menuitem', { name: /Open widget/ }).hover();
  await page.getByRole('menuitem', { name: /^Autospreader/ }).click();
  await page.waitForFunction((n) => document.querySelectorAll('section.frame .ase').length > n, before.length);
  const mine = (await ids()).find((i) => !before.includes(i));
  const ase = page.locator(`section.frame[data-widget-id="${mine}"]`);
  const row = ase.getByRole('row').filter({ has: page.getByText(name, { exact: true }) });
  await row.waitFor({ timeout: 5000 });
  await ase.evaluate((el) => el.scrollIntoView());
  await row.getByRole('button', { name: 'Chart' }).click();
  // its window is titled with the spread's name (an earlier run's chart may still be open)
  const chart = page.locator(`section.frame[aria-label="${name} 5m"]`);
  const note = chart.getByRole('note').filter({ hasText: 'spread mark' });
  // a cold start reads each leg's recorded day first: allow for it
  await note.waitFor({ state: 'attached', timeout: 90000 }).catch(async (e) => {
    await page.screenshot({ path: `${out}/spread-chart-failed.png` });
    console.log(await page.locator('section.frame').evaluateAll((els) => els.map((f) => f.getAttribute('aria-label') + ' :: ' + f.innerText.slice(0, 200).replace(/\n/g, ' | ')).join('\n')));
    throw e;
  });
  await note.evaluate((el) => el.closest('section.frame').scrollIntoView()); // a new window may open off screen
  check('the chart window is titled with the spread\'s name', (await chart.count()) === 1, `${name} 5m`);
  check('the chart opens on the spread and says it is a mark from its legs', (await note.innerText()).startsWith('spread mark, not traded prices'), await note.innerText());
  await page.waitForTimeout(2000);
  await page.screenshot({ path: `${out}/spread-chart.png` });

  const q = '?interval=1m';
  const [s, o, n] = await Promise.all([spread.instrument_id, OCT, NOV].map((i) => api(`/api/charts/${encodeURIComponent(i)}/candles${q}`)));
  const legs = [o, n].map((x) => Object.fromEntries(x.candles.map((c) => [c.t, c])));
  const wrong = s.candles.filter((c) => !legs[0][c.t] || !legs[1][c.t] || c.o !== legs[0][c.t].o - legs[1][c.t].o || c.c !== legs[0][c.t].c - legs[1][c.t].c);
  check('its candles are the legs\' together, bucket by bucket', s.candles.length > 0 && wrong.length === 0, `${s.candles.length} candles, ${wrong.length} differ, ${s.gaps} gaps`);
  check('no errors in the page', errors.length === 0, JSON.stringify(errors));
  await browser.close();
  const passed = results.filter(Boolean).length;
  console.log(`\n${passed}/${results.length} passed`);
  process.exit(passed === results.length ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
