// Real browser check of K11.4 on the demo (which records its ticks): the Recordings widget
// (days, sizes, a day's instruments), then Backtests: the New form drawn from the server, a
// TWAP run started from it over the recorded day(s), followed until done, its results (totals,
// curve, days), a day's curve and trades with the CSV and PDF saved by the browser, a second
// run, and Compare. Run with the demo up (it must have run for a few seconds to record) and
// ui/dist rebuilt.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { execSync } = require('child_process');
const fs = require('fs');
const flat = (s) => s.replace(/\s+/g, ' ').trim();
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 }, acceptDownloads: true });
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

  // --- Recordings
  await page.getByRole('button', { name: 'Widgets ▾' }).click();
  await page.getByRole('menuitem', { name: /Recordings/ }).click();
  const days = page.getByRole('grid', { name: 'Recorded days' });
  await days.getByRole('row').first().waitFor({ timeout: 10000 });
  console.log('recordings:', flat(await days.getByRole('row').first().textContent()));
  await days.getByRole('row').first().click();
  const instruments = page.getByRole('list', { name: /Instruments on/ });
  await instruments.waitFor({ timeout: 5000 });
  console.log('instruments:', (await instruments.getByRole('listitem').count()), 'busiest:', flat(await instruments.getByRole('listitem').first().textContent()));

  // --- Backtests: New
  await page.getByRole('button', { name: 'Widgets ▾' }).click();
  await page.getByRole('menuitem', { name: /Backtests/ }).click();
  await page.locator('.tab', { hasText: 'Backtests' }).dblclick(); // maximize
  await page.waitForTimeout(400);
  const bt = page.locator('.research').last();
  await bt.getByRole('button', { name: 'New' }).click();
  const form = page.getByRole('form', { name: 'Start a backtest' });
  await form.waitFor({ timeout: 10000 });
  await form.getByRole('combobox', { name: 'Algo' }).selectOption('TWAP');
  console.log('TWAP settings:', (await form.locator('.algo-form-row').nth(2).locator('.algo-field > span').allTextContents()).join(' | '));
  await form.getByLabel('Backtest contract').fill('NIFTY26OCT');
  await page.getByRole('option', { name: /NIFTY26OCTFUT/ }).first().click();
  await form.getByRole('textbox', { name: 'Lots' }).fill('2');
  await form.getByRole('textbox', { name: 'Start time' }).fill('00:00'); // the demo records at any hour
  for (const [label, value] of [['Over', '1'], ['Decide every', '10']]) {
    const box = form.getByRole('textbox', { name: new RegExp(`^${label}`) });
    if (await box.count()) await box.first().fill(value);
  }
  await form.getByRole('textbox', { name: 'Run name' }).fill('e2e twap');
  await form.getByRole('button', { name: 'All' }).click();
  const submit = form.getByRole('button', { name: /^Backtest TWAP/ });
  console.log('submit:', flat(await submit.textContent()));
  await submit.click();
  await page.waitForTimeout(800);
  if (await form.getByRole('alert').count()) console.log('form says:', flat(await form.getByRole('alert').textContent()));

  // --- followed until done
  const runs = page.getByRole('grid', { name: 'Backtest runs' });
  await runs.getByRole('row').first().waitFor({ timeout: 5000 });
  console.log('first state:', flat(await runs.getByRole('row').first().textContent()));
  await page.waitForFunction(() => {
    const row = document.querySelector('[aria-label="Backtest runs"] [role=row]');
    return row && /Done|Failed/.test(row.textContent);
  }, null, { timeout: 60000 });
  console.log('finished:', flat(await runs.getByRole('row').first().textContent()));
  const detail = page.getByRole('region', { name: /^Backtest BT/ });
  const totals = detail.getByLabel('Run totals');
  await totals.waitFor({ timeout: 5000 });
  console.log('totals:', flat(await totals.textContent()));
  console.log('curve:', await detail.getByRole('img').first().getAttribute('aria-label'));
  const dayRows = detail.getByRole('grid', { name: 'Backtest days' }).getByRole('row');
  console.log('days:', await dayRows.count(), '|', flat(await dayRows.first().textContent()));
  await dayRows.first().click();
  const day = detail.getByRole('region', { name: /^Day / });
  await day.waitFor({ timeout: 5000 });
  console.log('day curve:', await day.getByRole('img').getAttribute('aria-label'));
  const trades = day.getByRole('grid', { name: 'Backtest trades' }).getByRole('row');
  console.log('trades:', await trades.count(), '| first:', flat(await trades.first().textContent()));
  const svg = day.getByRole('img');
  await svg.scrollIntoViewIfNeeded();
  const box = await svg.boundingBox();
  await svg.hover({ position: { x: box.width * 0.6, y: box.height / 2 } });
  console.log('hover:', flat(await day.getByRole('status').textContent()));
  await page.screenshot({ path: '/tmp/claude-0/e2e-research-run.png' });
  for (const fmt of ['CSV', 'PDF']) {
    const [download] = await Promise.all([page.waitForEvent('download'), dayRows.first().getByRole('link', { name: fmt }).click()]);
    const file = `/tmp/claude-0/${download.suggestedFilename()}`;
    await download.saveAs(file);
    const bytes = fs.readFileSync(file);
    if (fmt === 'CSV') console.log('csv:', download.suggestedFilename(), bytes.toString().split('\n').slice(0, 6).join(' | '));
    else console.log('pdf:', download.suggestedFilename(), bytes.slice(0, 8).toString(), 'pages', (bytes.toString('latin1').match(/\/Count (\d+)/) || [])[1]);
  }

  // --- a second run (SELL), then Compare
  await bt.getByRole('button', { name: 'New' }).click();
  await form.waitFor({ timeout: 5000 });
  await form.getByRole('combobox', { name: 'Algo' }).selectOption('TWAP');
  await form.getByLabel('Backtest contract').fill('NIFTY26OCT');
  await page.getByRole('option', { name: /NIFTY26OCTFUT/ }).first().click();
  await form.getByRole('button', { name: 'SELL' }).click();
  await form.getByRole('textbox', { name: 'Start time' }).fill('00:00');
  await form.getByRole('textbox', { name: 'Run name' }).fill('e2e twap sell');
  await form.getByRole('textbox', { name: /^Over/ }).fill('2'); // no default: the form asks for it
  await form.getByRole('button', { name: 'All' }).click();
  await form.getByRole('button', { name: /^Backtest TWAP/ }).click();
  await page.waitForTimeout(800);
  if (await form.getByRole('alert').count()) console.log('form says:', flat(await form.getByRole('alert').textContent()));
  console.log('rows now:', (await runs.getByRole('row').allTextContents()).map(flat).join(' || '));
  await page.waitForFunction(() => {
    const rows = document.querySelectorAll('[aria-label="Backtest runs"] [role=row]');
    return rows.length >= 2 && [...rows].slice(0, 2).every((r) => /Done|Failed/.test(r.textContent));
  }, null, { timeout: 60000 });
  await bt.getByRole('button', { name: 'Compare' }).click();
  await page.getByRole('checkbox', { name: 'Compare e2e twap sell', exact: true }).first().check(); // the newest
  await page.getByRole('checkbox', { name: 'Compare e2e twap', exact: true }).first().check();
  const compare = page.getByRole('region', { name: 'Compare runs' });
  await compare.waitFor({ timeout: 5000 });
  console.log('legend:', flat(await compare.getByLabel('Legend').textContent()));
  for (const row of await compare.getByRole('table').getByRole('row').all()) console.log('  ', flat(await row.textContent()));
  await page.screenshot({ path: '/tmp/claude-0/e2e-research-compare.png' });
  console.log('page errors:', errors.length ? errors : 'none');
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
