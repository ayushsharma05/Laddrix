// Real-browser check of the spread ladder (#416) on the demo's paper account:
// define a NIFTY Oct/Nov calendar, open its ladder from the Autospreader, buy 1 spread lot at
// the spread's ask (it fills at once), and see both legs fill: +75 Oct, −75 Nov. Then a
// resting spread order shows in Work and is cancelled; the right-click menu opens the
// spread's Autospreader settings.
//
//   python3 -m mocktrade.tools.demo --port 8000 --ui ui/dist      (saved layouts and spreads cleared)
//   node context/scripts/e2e-spread-ladder.cjs <folder for screenshots>
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
  const context = await browser.newContext({ viewport: { width: 1600, height: 950 } });
  const page = await context.newPage();
  const errors = [];
  const expected = /\/api\/(layouts\/current|replay)$/;
  page.on('console', (m) => { if (m.type() === 'error' && !/401|404|422/.test(m.text())) errors.push(m.text()); });
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
  const held = async () => {
    const rows = await api('/api/accounts/acc-paper/positions');
    const list = Array.isArray(rows) ? rows : rows.positions ?? [];
    const of = (id) => list.filter((p) => p.instrument_id === id).reduce((s, p) => s + p.qty, 0);
    return { oct: of('NFO:NIFTY26OCTFUT'), nov: of('NFO:NIFTY26NOVFUT') };
  };
  const before = await held();

  // a calendar, made through the API (the Autospreader's own check covers the form)
  const made = await page.evaluate(async () => {
    const r = await fetch('/api/spreads', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({
      name: 'NIFTY Oct/Nov', legs: [
        { instrument_id: 'NFO:NIFTY26OCTFUT', ratio: 1, multiplier: 1, quote: true, payup_ticks: 2 },
        { instrument_id: 'NFO:NIFTY26NOVFUT', ratio: -1, multiplier: -1, payup_ticks: 2 },
      ] }) });
    return r.json();
  });
  check('a calendar spread is defined', !!made.spread_id, made.name);

  // the Autospreader, from the page's right-click menu; its Ladder button opens the spread ladder
  await page.mouse.click(1560, 880, { button: 'right' });
  await page.locator('.context-menu').getByRole('menuitem', { name: /Open widget/ }).hover();
  await page.getByRole('menuitem', { name: /^Autospreader/ }).click();
  const ase = page.locator('section.frame:has(.ase)');
  await ase.getByText('NIFTY Oct/Nov').waitFor({ timeout: 5000 });
  await ase.getByRole('button', { name: 'Ladder' }).click();
  const ladder = page.locator('section.frame:has(.ladder-spread-note)');
  await ladder.waitFor({ timeout: 5000 });
  await ladder.evaluate((el) => el.scrollIntoView());
  await ladder.locator('.price.last').waitFor({ timeout: 15000 });
  const title = await ladder.getAttribute('aria-label');
  check('the spread ladder opens, titled by the spread\'s name', title === 'NIFTY Oct/Nov', title);
  await page.waitForFunction(() => document.querySelectorAll('.ladder-spread-note').length && [...document.querySelectorAll('section.frame:has(.ladder-spread-note) [aria-label^="Sell at"]')].some((c) => c.textContent.trim() !== ''), null, { timeout: 15000 });
  await page.screenshot({ path: `${out}/spread-ladder-1.png` });

  // buy 1 spread lot at the best ask (Ctrl+click joins it): it fills at once, both legs
  await ladder.locator('.ladder-lots').evaluate(() => {});
  const lotsNow = Number(await ladder.locator('.ladder-lots').innerText());
  if (lotsNow !== 1) {
    await ladder.focus();
    await page.keyboard.press('Escape'); // clears the lots box
    await ladder.getByRole('button', { name: '+1', exact: true }).click();
  }
  const ask = ladder.locator('[aria-label^="Sell at"]').filter({ hasText: /\d/ }).first();
  const askLabel = await ask.getAttribute('aria-label');
  const askPrice = askLabel.replace('Sell at ', '');
  await ladder.getByLabel(`Buy at ${askPrice}`).click();
  let after = before;
  for (let i = 0; i < 40 && (after.oct === before.oct || after.nov === before.nov); i++) {
    await page.waitForTimeout(250);
    after = await held();
  }
  check('buying 1 spread lot at its ask fills both legs: +75 Oct, −75 Nov',
    after.oct - before.oct === 75 && after.nov - before.nov === -75, `Oct ${before.oct}→${after.oct}, Nov ${before.nov}→${after.nov}`);
  const algos = await api('/api/accounts/acc-paper/algos');
  const mine = algos.filter((a) => a.code === 'ASE').at(-1);
  const traded = mine?.extra?.traded_spread_paise;
  const level = mine?.params?.level_paise;
  check('the Autospreader algo is done; the spread traded is at or better than the price asked',
    mine?.state === 'DONE' && typeof traded === 'number' && traded <= level, `${mine?.state} ${mine?.extra?.legs} traded ${traded} for ${level}`);

  // a resting buy far below the market shows in Work; select and cancel it
  const bidCell = ladder.locator('[aria-label^="Buy at"]').last();
  const lowPrice = (await bidCell.getAttribute('aria-label')).replace('Buy at ', '');
  await bidCell.click();
  await ladder.getByLabel(`Select my orders at ${lowPrice}`).waitFor({ timeout: 5000 });
  check('a resting spread order shows in Work at its spread price', true, lowPrice);
  await page.screenshot({ path: `${out}/spread-ladder-2-working.png` });
  await ladder.getByLabel(`Select my orders at ${lowPrice}`).click();
  await ladder.getByLabel(`Cancel my orders at ${lowPrice}`).click();
  await ladder.getByLabel(`Select my orders at ${lowPrice}`).waitFor({ state: 'detached', timeout: 5000 });
  check('… and cancels (its algo stops)', true);

  // right-click on the spread ladder: Autospreader settings opens its definition to edit
  const head = await ladder.locator('.ladder-status').boundingBox();
  await page.mouse.click(head.x + 30, head.y + 6, { button: 'right' });
  const items = await page.locator('.context-menu > .cm-item .cm-label').allInnerTexts();
  check('right-click: the ladder\'s settings and Autospreader settings', items[0] === 'Autospreader settings…' && items.includes('Lots per click'), items.slice(0, 4).join(' | '));
  await page.screenshot({ path: `${out}/spread-ladder-3-menu.png` });
  await page.getByRole('menuitem', { name: 'Autospreader settings…' }).click();
  await page.getByRole('form', { name: 'Edit NIFTY Oct/Nov' }).waitFor({ timeout: 5000 });
  check('… which opens the spread in the Autospreader, ready to edit', true);

  check('no errors in the page', errors.length === 0, JSON.stringify(errors));
  await browser.close();
  const passed = results.filter(Boolean).length;
  console.log(`\n${passed}/${results.length} passed`);
  process.exit(passed === results.length ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
