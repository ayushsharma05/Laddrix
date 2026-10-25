// Real-browser check of the app's own right-click menu (#412): the page's menu on empty space,
// a widget's own settings on a widget, opening widgets from either, text boxes keeping the
// browser's menu, the keyboard, and a popped-out window having the same menu.
//
//   python3 -m laddrix.tools.demo --port 8000 --ui ui/dist      (saved layouts cleared)
//   node context/scripts/e2e-context-menu.cjs <folder for screenshots>
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
  page.on('console', (m) => { if (m.type() === 'error' && !/401|404/.test(m.text())) errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('response', (r) => { if (r.status() === 404 && !expected.test(new URL(r.url()).pathname)) errors.push(`404 ${r.url()}`); });

  await page.goto('http://localhost:8000/');
  await page.getByLabel('Username').fill('demo');
  await page.getByLabel('Password').fill('demo-password-123');
  await page.getByLabel('Authenticator code').fill(execFileSync('python3', ['-m', 'laddrix.tools.demo', '--code']).toString().trim());
  await page.keyboard.press('Enter');
  await page.locator('.frame .ladder .price.last').first().waitFor({ timeout: 20000 });
  const menu = page.locator('.context-menu').first();
  const items = async () => (await menu.locator(':scope > .cm-item .cm-label, :scope > .cm-header').allInnerTexts());

  // empty space (the right of the free page) -> the page's menu
  await page.mouse.click(1560, 400, { button: 'right' });
  await menu.waitFor({ timeout: 3000 });
  let shown = await items();
  check('right-click on empty space: the page menu', shown.includes('Open widget') && shown.includes('This page') && shown.includes('Theme'), shown.join(' | '));
  await page.screenshot({ path: `${out}/menu-1-page.png` });
  await menu.getByRole('menuitem', { name: /Open widget/ }).hover();
  await page.getByRole('menuitem', { name: /^Chart/ }).click();
  const chart = page.getByRole('region', { name: 'Chart', exact: true });
  await chart.waitFor({ timeout: 3000 });
  const box = await chart.evaluate((el) => ({ x: el.offsetLeft, y: el.offsetTop }));
  check('a widget opened from it lands where the page was clicked', Math.abs(box.x - 1552) <= 12 && Math.abs(box.y - 400 + 92) <= 120, `${box.x},${box.y}`);
  check('no browser menu, ours closed after choosing', (await page.locator('.context-menu').count()) === 0);

  // a ladder -> its own settings first, then open-for-this-contract, link group, …
  const ladder = page.locator('section.frame:has(.ladder)').first();
  const ladderName = await ladder.getAttribute('aria-label');
  await page.getByTestId('free-canvas').evaluate((el) => el.scrollTo(0, 0)); // the new chart scrolled the page to show it
  const lb = await ladder.locator('.ladder-status').boundingBox();
  await page.mouse.click(lb.x + 40, lb.y + 6, { button: 'right' });
  await menu.waitFor({ timeout: 3000 });
  shown = await items();
  check('right-click on a ladder: its own settings first', shown[0].includes(ladderName) && shown[1] === 'Lots per click' && shown[2] === 'Confirm orders over', shown.join(' | '));
  check('… then what any widget offers', ['Open for this contract', 'Link group', 'Duplicate', 'Pop out into its own window', 'Close'].every((l) => shown.includes(l)));
  await menu.getByRole('menuitem', { name: /Lots per click/ }).hover();
  await page.getByRole('menuitemcheckbox', { name: '5', exact: true }).click();
  await page.waitForTimeout(300);
  const lotsBox = await ladder.locator('.ladder-lots input, .ladder-lots').first().evaluate((el) => (el.value ?? el.textContent ?? '').trim());
  check('a setting chosen in the menu changes the ladder', /^5$|\b5\b/.test(lotsBox), `lots shows "${lotsBox}"`);
  await page.mouse.click(lb.x + 40, lb.y + 6, { button: 'right' });
  await menu.getByRole('menuitem', { name: /Open for this contract/ }).hover();
  await page.screenshot({ path: `${out}/menu-2-ladder.png` });
  const before = await page.locator('section.frame').count();
  await page.getByRole('menuitem', { name: /^Time & Sales|^Time and sales|^Depth/ }).first().click();
  check('opens another widget for the same contract', (await page.locator('section.frame').count()) === before + 1);

  // a text box keeps the browser's own menu (copy, paste)
  await page.getByPlaceholder('Search product  ( / )').click({ button: 'right' });
  await page.waitForTimeout(200);
  check('a text box keeps the browser menu (ours stays shut)', (await page.locator('.context-menu').count()) === 0);
  await page.keyboard.press('Escape');

  // the keyboard: Shift+F10 on a focused title bar opens the widget's menu; Esc closes
  await ladder.locator('.frame-title').focus();
  await page.keyboard.press('Shift+F10');
  await menu.waitFor({ timeout: 3000 });
  check('Shift+F10 opens the focused widget\'s menu', (await items())[1] === 'Lots per click');
  await page.keyboard.press('ArrowDown');
  await page.keyboard.press('Escape');
  check('Esc closes it', (await page.locator('.context-menu').count()) === 0);

  // page tabs: rename from the menu
  await page.locator('.page-tab.active').click({ button: 'right' });
  await menu.getByRole('menuitem', { name: /This page/ }).hover();
  await page.getByRole('menuitem', { name: /Rename/ }).click();
  const name = page.getByLabel('Page name');
  await name.fill('Scalping');
  await name.press('Enter');
  check('rename the page from its menu', (await page.locator('.page-tab.active').innerText()).startsWith('Scalping'));

  // a popped-out window has the same menu
  const [popup] = await Promise.all([
    context.waitForEvent('page'),
    ladder.getByRole('button', { name: 'Pop out' }).click(),
  ]);
  await popup.waitForLoadState();
  await popup.locator('.ladder .price.last').waitFor({ timeout: 10000 });
  await popup.locator('.ladder-status').click({ button: 'right' });
  await popup.locator('.context-menu').waitFor({ timeout: 3000 });
  const popItems = await popup.locator('.context-menu > .cm-item .cm-label').allInnerTexts();
  check('a popped-out window has the widget menu too', popItems.includes('Lots per click') && popItems.includes('Put back on the page'), popItems.join(' | '));
  await popup.getByRole('menuitem', { name: /Put back on the page/ }).click();
  await page.waitForTimeout(800);
  check('… and it puts the widget back', popup.isClosed());

  check('no errors in the page', errors.length === 0, JSON.stringify(errors));
  await browser.close();
  const passed = results.filter(Boolean).length;
  console.log(`\n${passed}/${results.length} passed`);
  process.exit(passed === results.length ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(1); });
