// Real-browser check of free pages (#410): windows anywhere, any size, gaps, scrolling both
// ways, snapping (Alt: none), the page keeps it all after a reload, and switching a page
// between free windows and docked tiles keeps its widgets.
//
//   python3 -m laddrix.tools.demo --port 8000 --ui ui/dist      (first-run layout: free)
//   node context/scripts/e2e-free-canvas.cjs <folder for screenshots>
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
  // expected answers: no saved layout yet on the first run, and no replay on a live demo
  const expected = /\/api\/(layouts\/current|replay)$/;
  page.on('console', (m) => { if (m.type() === 'error' && !/401|404/.test(m.text())) errors.push(m.text()); });
  page.on('response', (r) => { if (r.status() === 404 && !expected.test(new URL(r.url()).pathname)) errors.push(`404 ${r.url()}`); });
  page.on('pageerror', (e) => errors.push(String(e)));

  await page.goto('http://localhost:8000/');
  await page.getByLabel('Username').fill('demo');
  await page.getByLabel('Password').fill('demo-password-123');
  const code = execFileSync('python3', ['-m', 'laddrix.tools.demo', '--code']).toString().trim();
  await page.getByLabel('Authenticator code').fill(code);
  await page.keyboard.press('Enter');
  await page.locator('.frame .ladder .price.last').first().waitFor({ timeout: 20000 });

  const canvas = page.getByTestId('free-canvas');
  const frames = page.locator('section.frame');
  const boxOf = (name) => page.getByRole('region', { name, exact: true }).first().evaluate((el) => ({
    x: el.offsetLeft, y: el.offsetTop, w: el.offsetWidth, h: el.offsetHeight, z: Number(el.style.zIndex),
  }));
  check('first-run Main is a free page with starter windows', (await frames.count()) >= 5, `${await frames.count()} windows`);
  await page.screenshot({ path: `${out}/free-1-start.png` });

  // drag the order book by its title bar far below the screen: the page grows and scrolls
  const book = page.getByRole('region', { name: 'Order Book', exact: true });
  const title = book.locator('.frame-title');
  let t = await title.boundingBox();
  await page.mouse.move(t.x + 80, t.y + 10);
  await page.mouse.down();
  await page.mouse.move(t.x + 300, t.y + 300, { steps: 8 });
  await page.mouse.move(t.x + 301, t.y + 1700, { steps: 30 }); // past the bottom: the page scrolls as it goes
  await page.mouse.up();
  let b = await boxOf('Order Book');
  const scroll = await canvas.evaluate((el) => ({ h: el.scrollHeight, client: el.clientHeight, top: el.scrollTop, w: el.scrollWidth, cw: el.clientWidth }));
  check('a window can sit below the screen', b.y > scroll.client, `y ${b.y}, screen ${scroll.client}`);
  check('the page scrolls down to it', scroll.h > scroll.client && scroll.top > 0, `scrollHeight ${scroll.h}, scrolled ${scroll.top}`);
  check('it landed on the 8 px grid', b.x % 8 === 0 && b.y % 8 === 0, `${b.x},${b.y}`);

  // and far to the right: horizontal scrolling too
  await canvas.evaluate((el) => el.scrollTo(0, 0));
  const positions = page.getByRole('region', { name: 'Positions', exact: true });
  t = await positions.locator('.frame-title').boundingBox();
  await page.mouse.move(t.x + 60, t.y + 10);
  await page.mouse.down();
  await page.mouse.move(t.x + 400, t.y + 10, { steps: 6 });
  await page.mouse.move(t.x + 2400, t.y + 10, { steps: 40 });
  await page.mouse.up();
  const p = await boxOf('Positions');
  const sideways = await canvas.evaluate((el) => ({ w: el.scrollWidth, cw: el.clientWidth, left: el.scrollLeft }));
  check('a window can sit right of the screen, and the page scrolls sideways', p.x + p.w > sideways.cw && sideways.w > sideways.cw && sideways.left > 0,
    `x ${p.x}, scrollWidth ${sideways.w}`);
  await page.screenshot({ path: `${out}/free-2-scrolled.png` });
  await canvas.evaluate((el) => el.scrollTo(0, 0));

  // snap: drop the ticket 5 px from the first ladder's right edge; it sticks 4 px beside it
  const ladder = await boxOf('NIFTY26OCTFUT').catch(() => null);
  const ladderName = ladder ? 'NIFTY26OCTFUT' : await page.locator('section.frame:has(.ladder)').first().getAttribute('aria-label');
  const L = await boxOf(ladderName);
  const ticketName = await page.locator('section.frame:has(.ticket)').first().getAttribute('aria-label');
  const T0 = await boxOf(ticketName);
  t = await page.getByRole('region', { name: ticketName, exact: true }).locator('.frame-title').boundingBox();
  const wantX = L.x + L.w + 4;
  const dx = wantX + 5 - T0.x; // 5 px off the snapped place
  await page.mouse.move(t.x + 60, t.y + 10);
  await page.mouse.down();
  await page.mouse.move(t.x + 60 + dx / 2, t.y + 300, { steps: 5 });
  await page.mouse.move(t.x + 60 + dx, t.y + 300 + (L.y + 200 - T0.y - 300), { steps: 5 });
  await page.mouse.up();
  let T = await boxOf(ticketName);
  check('a window snaps beside its neighbour with a 4 px gap', T.x === wantX, `x ${T.x}, wanted ${wantX}`);

  // Alt held: no snapping, it goes exactly where the mouse is
  t = await page.getByRole('region', { name: ticketName, exact: true }).locator('.frame-title').boundingBox();
  await page.keyboard.down('Alt');
  await page.mouse.move(t.x + 60, t.y + 10);
  await page.mouse.down();
  await page.mouse.move(t.x + 63, t.y + 13, { steps: 3 });
  await page.mouse.up();
  await page.keyboard.up('Alt');
  const T2 = await boxOf(ticketName);
  check('with Alt held it is placed freely (3 px, no snap)', T2.x === T.x + 3 && T2.y === T.y + 3, `${T.x},${T.y} -> ${T2.x},${T2.y}`);

  // resize from the bottom-right corner: any size
  const se = page.getByRole('region', { name: ticketName, exact: true }).locator('.rz-se');
  const c = await se.boundingBox();
  await page.mouse.move(c.x + 5, c.y + 5);
  await page.mouse.down();
  await page.mouse.move(c.x + 205, c.y + 125, { steps: 6 });
  await page.mouse.up();
  const T3 = await boxOf(ticketName);
  check('a corner resizes the window', T3.w >= T2.w + 190 && T3.h >= T2.h + 110, `${T2.w}x${T2.h} -> ${T3.w}x${T3.h}`);

  // the window clicked last is in front
  await page.getByRole('region', { name: ladderName, exact: true }).locator('.frame-title').click();
  const front = await boxOf(ladderName);
  const zs = await frames.evaluateAll((els) => els.map((e) => Number(e.style.zIndex)));
  check('a clicked window comes to the front', front.z === Math.max(...zs), `z ${front.z} of ${zs.join(',')}`);

  // open another widget: it is added on the page; any number can be open
  const before = await frames.count();
  for (let i = 0; i < 3; i++) {
    await page.getByRole('button', { name: /Widgets/ }).click();
    await page.getByRole('menuitem', { name: /^Chart/ }).first().click();
  }
  check('more widgets open as windows', (await frames.count()) === before + 3, `${before} -> ${await frames.count()}`);

  // a reload keeps every place and size
  const saved = await frames.evaluateAll((els) => els.map((e) => [e.getAttribute('aria-label'), e.offsetLeft, e.offsetTop, e.offsetWidth, e.offsetHeight]));
  await page.waitForFunction(() => /layout saved/.test(document.querySelector('.page-status')?.textContent ?? ''), null, { timeout: 10000 });
  await page.reload();
  await page.locator('section.frame').first().waitFor({ timeout: 20000 });
  await page.waitForTimeout(500);
  const restored = await frames.evaluateAll((els) => els.map((e) => [e.getAttribute('aria-label'), e.offsetLeft, e.offsetTop, e.offsetWidth, e.offsetHeight]));
  check('after a reload every window is where it was, the same size', JSON.stringify(restored) === JSON.stringify(saved), `${restored.length} windows`);
  await page.screenshot({ path: `${out}/free-3-reloaded.png` });

  // switch the page to docked tiles and back: the widgets stay
  const count = await frames.count();
  await page.getByRole('button', { name: 'Switch this page to docked tiles' }).click();
  await page.locator('.dv-groupview').first().waitFor({ timeout: 5000 });
  const tiles = await page.locator('.dv-tab').count();
  check('docked: the same widgets as tiles that fill the page', tiles === count && (await frames.count()) === 0, `${tiles} tiles`);
  await page.screenshot({ path: `${out}/free-4-docked.png` });
  await page.getByRole('button', { name: 'Switch this page to a free layout' }).click();
  await page.locator('section.frame').first().waitFor({ timeout: 5000 });
  check('free again: the same widgets as windows', (await frames.count()) === count, `${await frames.count()} windows`);
  await page.screenshot({ path: `${out}/free-5-from-docked.png` });

  // pop a window out into its own browser window, and back
  const [popup] = await Promise.all([
    context.waitForEvent('page'),
    page.getByRole('region', { name: ladderName, exact: true }).getByRole('button', { name: 'Pop out' }).click(),
  ]);
  await popup.waitForLoadState();
  await popup.locator('.ladder .price.last').waitFor({ timeout: 10000 });
  check('a free window pops out with its widget live', true);
  await popup.getByRole('button', { name: 'Dock back' }).click();
  await page.waitForTimeout(800);
  check('and docks back onto the page', popup.isClosed() && (await page.getByRole('region', { name: ladderName, exact: true }).count()) === 1);

  check('no errors in the page', errors.length === 0, JSON.stringify(errors));
  await browser.close();
  const passed = results.filter(Boolean).length;
  console.log(`\n${passed}/${results.length} passed`);
  process.exit(passed === results.length ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
