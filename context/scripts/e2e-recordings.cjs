// Real browser check of K11.5 on the demo: an older recorded day is deleted from the Recordings
// widget after its confirmation (the files go, the audit trail says who); today's day offers no
// delete; the replay command shown can be copied. Before running: the demo up with ui/dist
// rebuilt, and an older day present (cp -r data/demo/ticks/<today> data/demo/ticks/<older>).
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { execSync } = require('child_process');
const fs = require('fs');
const flat = (s) => s.replace(/\s+/g, ' ').trim();
const older = process.argv[2] || '2026-10-15';
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: 'http://localhost:8000' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('http://localhost:8000/');
  await page.getByLabel('Username').fill('demo');
  await page.getByLabel('Password').fill('demo-password-123');
  await page.getByLabel('Authenticator code').fill(execSync('python3 -m mocktrade.tools.demo --code').toString().trim());
  await page.keyboard.press('Enter');
  await page.locator('section.frame').first().waitFor({ timeout: 20000 });
  await page.evaluate(async () => {
    await fetch('/api/layouts/current', { method: 'DELETE', credentials: 'same-origin' });
  });
  await page.reload();
  await page.locator('.ladder').first().waitFor({ timeout: 20000 });
  await page.getByRole('button', { name: 'Widgets ▾' }).click();
  await page.getByRole('menuitem', { name: /Recordings/ }).click();
  await page.locator('.tab', { hasText: 'Recordings' }).dblclick(); // maximize
  const days = page.getByRole('grid', { name: 'Recorded days' });
  await days.getByText(older, { exact: true }).waitFor({ timeout: 10000 });
  console.log('days before:', (await days.getByRole('row').allTextContents()).map(flat).join(' || '));
  const rows = days.getByRole('row');
  await rows.first().click(); // today
  const today = page.getByLabel(/^Actions for /);
  await today.waitFor({ timeout: 5000 });
  console.log('today actions:', flat(await today.textContent()), '| delete offered:', await today.getByRole('button', { name: 'Delete…' }).count());
  await today.getByRole('button', { name: 'Copy' }).click();
  console.log('clipboard:', await page.evaluate(() => navigator.clipboard.readText()));
  await days.getByText(older, { exact: true }).click();
  const old = page.getByLabel(`Actions for ${older}`);
  await old.getByRole('button', { name: 'Delete…' }).click();
  const confirm = old.getByRole('button', { name: /^Confirm: delete/ });
  console.log('confirm button:', flat(await confirm.textContent()), '| folder still there:', fs.existsSync(`data/demo/ticks/${older}`));
  await page.screenshot({ path: '/tmp/claude-0/e2e-recordings.png' });
  await confirm.click();
  await days.getByText(older, { exact: true }).waitFor({ state: 'detached', timeout: 5000 });
  console.log('days after:', (await days.getByRole('row').allTextContents()).map(flat).join(' || '));
  console.log('folder gone:', !fs.existsSync(`data/demo/ticks/${older}`));
  await page.waitForTimeout(500); // the audit trail is written in the background
  const sql = "SELECT detail FROM events WHERE kind='RECORDING_DELETED' ORDER BY seq DESC LIMIT 1";
  console.log('audit:', execSync(`python3 -c "import sqlite3; print(sqlite3.connect('data/demo/events.db').execute(\\"${sql}\\").fetchone())"`).toString().trim());
  console.log('page errors:', errors.length ? errors : 'none');
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
