// Real browser check of K12.2 on the demo: open a Chart from Widgets on the pink link, pick the
// NIFTY future, see candles painted (pixels checked), the forming candle move, a real paper fill
// marked, a click on the chart filling the pink ticket's price, drag to pan, wheel to zoom, and
// the 1m interval. Run with the demo up (a minute or more of recording helps) and ui/dist rebuilt.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { execSync } = require('child_process');
const flat = (s) => s.replace(/\s+/g, ' ').trim();
(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('http://localhost:8000/');
  await page.getByLabel('Username').fill('demo');
  await page.getByLabel('Password').fill('demo-password-123');
  await page.getByLabel('Authenticator code').fill(execSync('python3 -m laddrix.tools.demo --code').toString().trim());
  await page.keyboard.press('Enter');
  await page.locator('section.frame').first().waitFor({ timeout: 20000 });
  await page.evaluate(async () => {
    await fetch('/api/layouts/current', { method: 'DELETE', credentials: 'same-origin' });
  });
  await page.reload();
  await page.locator('.ladder').first().waitFor({ timeout: 20000 });
  await page.getByRole('button', { name: 'Widgets ▾' }).click();
  await page.getByRole('menuitem', { name: /^Chart/ }).click();
  const chart = page.locator('.chart').last();
  if (await page.getByLabel('Chart instrument').count()) {
    await page.getByLabel('Chart instrument').fill('NIFTY26OCT');
    await page.getByRole('option', { name: /NIFTY26OCTFUT/ }).first().click();
  }
  const readout = chart.getByRole('status', { name: 'Candle' });
  await page.waitForFunction(() => /C \d/.test(document.querySelector('.chart [aria-label="Candle"]')?.textContent || ''), null, { timeout: 10000 });
  const img = chart.getByRole('img');
  console.log('chart:', await img.getAttribute('aria-label'));
  console.log('readout:', flat(await readout.textContent()));
  console.log('intervals:', (await chart.getByRole('group', { name: 'Interval' }).getByRole('button').allTextContents()).join(' '));
  const painted = await img.evaluate((c) => {
    const ctx = c.getContext('2d');
    const d = ctx.getImageData(0, 0, c.width, c.height).data;
    const colours = new Set();
    for (let i = 0; i < d.length; i += 4 * 97) colours.add(`${d[i]},${d[i + 1]},${d[i + 2]}`);
    return { width: c.width, height: c.height, colours: colours.size };
  });
  console.log('canvas:', JSON.stringify(painted));
  const before = flat(await readout.textContent());
  await page.waitForTimeout(3000);
  console.log('3 s later:', flat(await readout.textContent()), '| changed:', before !== flat(await readout.textContent()));

  // a real fill, marked (towards flat, so repeated runs stay inside the position limit)
  const o = await page.evaluate(async () => {
    const pos = await (await fetch('/api/accounts/acc-paper/positions', { credentials: 'same-origin' })).json();
    const mine = (Array.isArray(pos) ? pos : pos.positions || []).filter((p) => p.instrument_id === 'NFO:NIFTY26OCTFUT').reduce((a, p) => a + p.qty, 0);
    const side = mine > 0 ? 'SELL' : 'BUY';
    const r = await fetch('/api/accounts/acc-paper/orders', { method: 'POST', credentials: 'same-origin', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ instrument_id: 'NFO:NIFTY26OCTFUT', side, qty: 75, order_type: 'MARKET', product: 'NRML', market_protection: -1 }) });
    const body = await r.json();
    return { status: r.status, side, position: mine, state: body.status, reason: body.reason };
  });
  console.log('order:', JSON.stringify(o));
  const sign = o.side === 'BUY' ? '▲' : '▼';
  await page.waitForFunction((s) => new RegExp(`${s} ${s === '▲' ? 'BUY' : 'SELL'} 75`).test(document.querySelector('.chart [aria-label="Candle"]')?.textContent || ''), sign, { timeout: 8000 });
  console.log('marked:', flat(await readout.textContent()).match(new RegExp(`${sign} [A-Z]+ 75[^▲▼]*`))[0]);
  await page.screenshot({ path: '/tmp/claude-0/e2e-chart.png' });

  // click a price: the pink ticket takes it
  const box = await img.boundingBox();
  await page.mouse.click(box.x + box.width * 0.4, box.y + box.height * 0.3);
  const hint = flat(await chart.locator('.chart-hint').textContent());
  console.log('hint:', hint);
  const picked = hint.match(/^([\d,.]+)/)[1];
  await page.waitForTimeout(300);
  const ticketPrice = await page.locator('input[name="price"]').first().inputValue();
  console.log('ticket price:', ticketPrice, '| same:', ticketPrice === picked);

  // window titles follow the instrument (the pink ticket and ladder switched with the chart's pick)
  console.log('titles:', (await page.locator('section.frame').evaluateAll((els) => els.map((e) => e.getAttribute('aria-label')))).filter((t) => /NIFTY|Ticket/.test(t)).join(' | '));

  // the 1m interval: zoom and pan (in the small panel, where zooming in hides candles)
  await chart.getByRole('group', { name: 'Interval' }).getByRole('button', { name: '1m' }).click();
  await page.waitForFunction(() => / 1m chart: \d+ of ([2-9]|\d\d+) /.test(document.querySelector('.chart canvas')?.getAttribute('aria-label') || ''), null, { timeout: 8000 });
  const label = async () => (await img.getAttribute('aria-label')).match(/(\d+) of (\d+) candles/).slice(1).map(Number);
  const big = await img.boundingBox();
  console.log('1m:', Math.round(big.width), 'x', Math.round(big.height), '|', await label());
  await page.mouse.move(big.x + big.width / 2, big.y + big.height / 2);
  for (let k = 0; k < 12; k++) await page.mouse.wheel(0, -100);
  await page.waitForTimeout(300);
  console.log('zoomed in:', await label());
  await page.mouse.move(big.x + big.width * 0.7, big.y + big.height / 2); // left of the price axis
  const newest = flat(await readout.textContent()).slice(0, 12);
  await page.mouse.move(big.x + big.width * 0.2, big.y + big.height / 2);
  await page.mouse.down();
  await page.mouse.move(big.x + big.width * 0.7, big.y + big.height / 2, { steps: 8 });
  await page.mouse.up();
  await page.waitForTimeout(500);
  await page.mouse.move(big.x + big.width * 0.7, big.y + big.height / 2);
  console.log('panned back:', await label(), '| under the pointer before', newest, '| now', flat(await readout.textContent()).slice(0, 12));
  await page.screenshot({ path: '/tmp/claude-0/e2e-chart-1m.png' });
  await page.keyboard.press('Home');
  await page.mouse.move(big.x + big.width / 2, big.y + big.height / 2);
  for (let k = 0; k < 12; k++) await page.mouse.wheel(0, 100);
  await page.waitForTimeout(300);
  console.log('zoomed out:', await label());
  console.log('page errors:', errors.length ? errors : 'none');
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
