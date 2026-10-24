// Real-browser check of the Autospreader (#414): define a NIFTY calendar spread (Oct − Nov)
// from the widget, see its live implied bid and ask, check them against the legs' own best
// prices, rename it, survive a reload and a server restart, and delete it.
//
//   python3 -m mocktrade.tools.demo --port 8000 --ui ui/dist      (saved layouts cleared)
//   node context/scripts/e2e-autospreader.cjs <folder for screenshots>
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { execFileSync } = require('child_process');
const out = process.argv[2] || '.';
const results = [];
const check = (name, ok, detail = '') => {
  results.push(ok);
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? `  (${detail})` : ''}`);
};
const rupees = (text) => Number(text.replace(/[(].*$/, '').replace(/,/g, '').replace('−', '-').trim()) * 100;

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

  // open the Autospreader from the right-click menu, on empty space
  await page.mouse.click(1560, 880, { button: 'right' });
  await page.locator('.context-menu').getByRole('menuitem', { name: /Open widget/ }).hover();
  await page.getByRole('menuitem', { name: /^Autospreader/ }).click();
  const ase = page.locator('section.frame:has(.ase)'); // its title changes while editing
  await ase.waitFor({ timeout: 5000 });
  await ase.evaluate((el) => el.scrollIntoView());
  check('the Autospreader opens from the menu', true);

  // New: the legs, then a mistake (no name): the server's words show
  await ase.getByRole('button', { name: 'New', exact: true }).click();
  await ase.getByLabel('Leg 1 contract').fill('NIFTY26OCT');
  await page.getByRole('option', { name: /NIFTY26OCTFUT/ }).first().dispatchEvent('mousedown');
  await ase.getByLabel('Leg 2 contract').fill('NIFTY26NOV');
  await page.getByRole('option', { name: /NIFTY26NOVFUT/ }).first().dispatchEvent('mousedown');
  await ase.getByRole('button', { name: 'Create spread' }).click();
  await ase.getByRole('alert').waitFor({ timeout: 3000 });
  check('a mistake is said in words', (await ase.getByRole('alert').innerText()).includes('the name must be 1 to 40 characters'));
  await ase.getByLabel('Spread name').fill('NIFTY Oct/Nov');
  check('the formula shows', (await ase.innerText()).includes('price = NIFTY26OCTFUT − NIFTY26NOVFUT'));
  await page.screenshot({ path: `${out}/ase-1-form.png` });
  await ase.getByRole('button', { name: 'Create spread' }).click();
  await ase.getByText('NIFTY Oct/Nov').waitFor({ timeout: 5000 });
  check('the spread is created and listed', (await ase.innerText()).includes('+1 NIFTY26OCTFUT −1 NIFTY26NOVFUT'));

  // its live implied price, checked against the legs' own best prices at the same moment
  const bid = ase.getByLabel('NIFTY Oct/Nov bid');
  const ask = ase.getByLabel('NIFTY Oct/Nov ask');
  await page.waitForFunction(() => {
    const b = document.querySelector('[aria-label="NIFTY Oct/Nov bid"]');
    return b && b.textContent !== '–';
  }, null, { timeout: 15000 });
  const legs = await page.evaluate(async () => {
    const one = async (id) => (await (await fetch(`/api/instruments/${encodeURIComponent(id)}`)).json()).ltp_paise;
    return { oct: await one('NFO:NIFTY26OCTFUT'), nov: await one('NFO:NIFTY26NOVFUT') };
  });
  const b = rupees(await bid.innerText());
  const a = rupees(await ask.innerText());
  check('a live bid and ask, bid below ask', Number.isFinite(b) && Number.isFinite(a) && b < a, `${await bid.innerText()} / ${await ask.innerText()}`);
  const mark = legs.oct - legs.nov;
  check('… near the legs\' own prices (Oct − Nov)', Math.abs((a + b) / 2 - mark) < 500, `mid ${(a + b) / 2}, legs ${mark}`);
  const first = await ask.innerText();
  let moved = first;
  for (let i = 0; i < 30 && moved === first; i++) {
    await page.waitForTimeout(500);
    moved = await ask.innerText();
  }
  check('it moves as the legs tick', moved !== first, `${first} -> ${moved}`);
  await page.screenshot({ path: `${out}/ase-2-live.png` });

  // edit: rename it
  await ase.getByRole('button', { name: 'Edit' }).click();
  await ase.getByLabel('Spread name').fill('NIFTY calendar');
  await ase.getByRole('button', { name: 'Save' }).click();
  await ase.getByText('NIFTY calendar').waitFor({ timeout: 5000 });
  check('renamed', true);

  // a reload keeps it (and the Autospreader window on the page)
  await page.waitForFunction(() => /layout saved/.test(document.querySelector('.page-status')?.textContent ?? ''), null, { timeout: 10000 });
  await page.reload();
  await page.locator('section.frame:has(.ase)').getByText('NIFTY calendar').waitFor({ timeout: 20000 });
  check('after a reload the spread is still there, priced', true);

  // delete: two clicks
  const again = page.locator('section.frame:has(.ase)');
  await again.getByRole('button', { name: 'Delete…' }).click();
  await again.getByRole('button', { name: 'Confirm delete' }).click();
  await again.getByText('No spreads yet').waitFor({ timeout: 5000 });
  check('deleted after a second click', true);

  check('no errors in the page', errors.length === 0, JSON.stringify(errors));
  await browser.close();
  const passed = results.filter(Boolean).length;
  console.log(`\n${passed}/${results.length} passed`);
  process.exit(passed === results.length ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
