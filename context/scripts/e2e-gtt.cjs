// Real server check of the K9.3 GTT API on the demo (paper broker), through a logged-in browser:
// the spec, a refused GTT (too near), a placed one, the list, then delete.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { execSync } = require('child_process');
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
  const page = await browser.newPage();
  await page.goto('http://localhost:8000/');
  await page.getByLabel('Username').fill('demo');
  await page.getByLabel('Password').fill('demo-password-123');
  await page.getByLabel('Authenticator code').fill(execSync('python3 -m laddrix.tools.demo --code').toString().trim());
  await page.keyboard.press('Enter');
  await page.locator('section.frame').first().waitFor({ timeout: 20000 });
  const api = (m, p, b) => page.evaluate(async ([m, p, b]) => {
    const r = await fetch(p, { method: m, credentials: 'same-origin', headers: { 'content-type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return { status: r.status, body: await r.json().catch(() => null) };
  }, [m, p, b]);
  const A = '/api/accounts/acc-paper';
  const FUT = 'NFO:NIFTY26OCTFUT';
  const spec = await api('GET', `${A}/gtt-spec?instrument_id=${FUT}`);
  console.log('spec', spec.status, JSON.stringify(spec.body.kinds), spec.body.min_distance_pct);
  const pos = await api('GET', `${A}/positions`);
  const book = await api('GET', `/api/instruments/${FUT}`).catch(() => null);
  const ltp = 2500000; // the simulated market sits near 25,000-25,010
  const leg = (trigger, price) => ({ trigger_paise: trigger, side: 'SELL', qty: 75, price_paise: price });
  const near = await api('POST', `${A}/gtt`, { instrument_id: FUT, product: 'NRML', kind: 'single', legs: [leg(ltp, ltp - 1000)] });
  console.log('too near:', near.status, near.body.detail);
  const placed = await api('POST', `${A}/gtt`, { instrument_id: FUT, product: 'NRML', kind: 'two-leg', legs: [leg(2470000, 2468000), leg(2540000, 2539000)] });
  console.log('placed:', placed.status, placed.body.gtt_id, placed.body.status, placed.body.kind);
  const list = await api('GET', `${A}/gtt`);
  console.log('list:', JSON.stringify(list.body.map((g) => [g.gtt_id, g.status])));
  const del = await api('DELETE', `${A}/gtt/${placed.body.gtt_id}`);
  console.log('deleted:', del.status, del.body.status);
  await page.waitForTimeout(6000); // a poll later, the broker still agrees
  const after = await api('GET', `${A}/gtt`);
  console.log('after poll:', JSON.stringify(after.body.map((g) => [g.gtt_id, g.status])));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
