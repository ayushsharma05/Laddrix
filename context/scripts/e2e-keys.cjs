// Real browser check of K13.4 on the demo (#284), with the demo up and ui/dist rebuilt: open
// Keyboard shortcuts from Widgets; a browser key is refused in words; "Open a chart" gets F2 and
// F2 opens a chart; STOP gets F9 and F9 opens the stop dialog while Ctrl+Shift+K still does; the
// choices survive a reload (saved on the server); "all back to the defaults" takes two clicks.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { execSync } = require('child_process');
const flat = (s) => (s || '').replace(/\s+/g, ' ').trim();
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
  const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('http://localhost:8000/');
  await page.getByLabel('Username').fill('demo');
  await page.getByLabel('Password').fill('demo-password-123');
  await page.getByLabel('Authenticator code').fill(execSync('python3 -m mocktrade.tools.demo --code').toString().trim());
  await page.keyboard.press('Enter');
  await page.locator('section.frame').first().waitFor({ timeout: 20000 });
  await page.evaluate(async () => { await fetch('/api/me/settings/keys', { method: 'PUT', credentials: 'same-origin', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ value: null }) }); });
  await page.getByRole('button', { name: 'Widgets ▾' }).click();
  await page.getByRole('menuitem', { name: /^Keyboard shortcuts/ }).click();
  const editor = page.locator('.keys-editor');
  await editor.waitFor({ timeout: 5000 });
  const chartRow = editor.getByRole('group', { name: 'Open a chart' });
  await chartRow.getByRole('button', { name: 'Add a key to Open a chart' }).click();
  await page.keyboard.press('Control+w');  // refused, and the tab stays open (the page is still here)
  console.log('Ctrl+W:', flat(await editor.getByRole('alert').textContent()));
  await page.keyboard.press('F2');
  await page.waitForTimeout(400);
  console.log('Open a chart keys:', flat(await chartRow.locator('.keys-list').textContent()));
  const stopRow = editor.getByRole('group', { name: 'STOP trading (opens the stop dialog)' });
  await stopRow.getByRole('button', { name: /^Add a key/ }).click();
  await page.keyboard.press('F9');
  await page.waitForTimeout(400);
  console.log('STOP keys:', flat(await stopRow.locator('.keys-list').textContent()));
  await page.screenshot({ path: '/tmp/claude-0/e2e-keys.png' });
  const chartsBefore = await page.locator('section.frame[aria-label^="Chart"], section.frame:has(.chart)').count();
  await page.locator('section.frame .frame-title').first().click();  // focus away from the editor
  await page.keyboard.press('F2');
  await page.waitForTimeout(800);
  console.log('F2 opened a chart:', (await page.locator('section.frame[aria-label^="Chart"], section.frame:has(.chart)').count()) === chartsBefore + 1);
  await page.keyboard.press('F9');
  console.log('F9 → stop dialog:', await page.getByRole('dialog').count());
  await page.keyboard.press('Escape');
  await page.waitForTimeout(200);
  await page.keyboard.press('Control+Shift+K');
  console.log('Ctrl+Shift+K still → stop dialog:', await page.getByRole('dialog').count());
  await page.keyboard.press('Escape');
  await page.reload();
  await page.locator('section.frame').first().waitFor({ timeout: 20000 });
  await page.waitForTimeout(800);
  const saved = await page.evaluate(async () => (await (await fetch('/api/me/settings/keys', { credentials: 'same-origin' })).json()).value);
  console.log('after a reload, saved on the server:', JSON.stringify(saved));
  const charts2 = await page.locator('section.frame[aria-label^="Chart"], section.frame:has(.chart)').count();
  await page.locator('section.frame .frame-title').first().click();
  await page.keyboard.press('F2');
  await page.waitForTimeout(800);
  console.log('F2 still opens a chart after the reload:', (await page.locator('section.frame[aria-label^="Chart"], section.frame:has(.chart)').count()) === charts2 + 1);
  if (!(await page.locator('.keys-editor').count())) {  // the layout wasn't saved: open it again
    await page.getByRole('button', { name: 'Widgets ▾' }).click();
    await page.getByRole('menuitem', { name: /^Keyboard shortcuts/ }).click();
  }
  const ed = page.locator('.keys-editor').first();
  await ed.getByRole('button', { name: 'All back to the defaults…' }).click();
  await ed.getByRole('button', { name: 'Confirm: back to the defaults' }).click();
  await page.waitForTimeout(400);
  const after = await page.evaluate(async () => (await (await fetch('/api/me/settings/keys', { credentials: 'same-origin' })).json()).value);
  console.log('after "back to the defaults":', JSON.stringify(after));
  console.log('page errors:', errors.length ? errors : 'none');
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
