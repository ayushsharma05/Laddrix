// The browser half of the crash drill (V1.2, #328), from the repo root; ui/dist must be built.
// It starts its own fresh demo (data/demo is wiped). It:
//   1. starts the demo with the screens, signs in, buys 75 and protects it with a trailing stop;
//   2. kills the server (SIGKILL) and starts it again: after signing in, the ACTION NEEDED band
//      says the position is UNPROTECTED and why (screenshot);
//   3. freezes the server (SIGSTOP) for 9 s: the PRICES … OLD band shows (screenshot); SIGCONT:
//      it goes away;
//   4. closes the position: the UNPROTECTED band goes away.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { execSync } = require('child_process');
const IMAGES = `${__dirname}/../images`;
const PATTERN = '[-]m laddrix[.]tools[.]demo';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const startDemo = async () => {
  execSync('nohup python3 -m laddrix.tools.demo --ui ui/dist >> data/drill-demo.log 2>&1 &');
  for (let i = 0; i < 60; i++) {
    try { execSync('curl -sf http://127.0.0.1:8000/api/health >/dev/null'); return; } catch { await sleep(500); }
  }
  throw new Error('the demo did not start');
};
const freshCode = async () => {
  const w = Math.floor(Date.now() / 30000);
  while (Math.floor(Date.now() / 30000) === w) await sleep(500);  // each code works once
  return execSync('python3 -m laddrix.tools.demo --code').toString().trim();
};
const pid = () => execSync(`pgrep -f "${PATTERN}"`).toString().trim().split('\n')[0];
(async () => {
  try { execSync(`pkill -f "${PATTERN}"`); } catch {}
  await sleep(1000);
  execSync('rm -rf data/demo');  // a clean start: nothing open from an earlier run
  await startDemo();
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
  const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  const signIn = async () => {
    await page.goto('http://localhost:8000/');
    const form = page.getByLabel('Username');
    await Promise.race([form.waitFor({ timeout: 20000 }), page.locator('section.frame').first().waitFor({ timeout: 20000 })]);
    if (!(await form.count())) return 'still signed in';  // the session outlived the restart
    await form.fill('demo');
    await page.getByLabel('Password').fill('demo-password-123');
    await page.getByLabel('Authenticator code').fill(await freshCode());
    await page.keyboard.press('Enter');
    await page.locator('section.frame').first().waitFor({ timeout: 20000 });
    return 'signed in';
  };
  const api = (method, path, body) => page.evaluate(async ([m, p, b]) => {
    const r = await fetch(p, { method: m, credentials: 'same-origin', headers: { 'content-type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return { status: r.status, body: await r.json().catch(() => null) };
  }, [method, path, body]);
  const bandsText = () => page.locator('.bands').innerText().catch(() => '');
  const FUT = 'NFO:NIFTY26OCTFUT';

  await signIn();
  const buy = await api('POST', '/api/accounts/acc-paper/orders', { instrument_id: FUT, side: 'BUY', qty: 75, order_type: 'MARKET', product: 'NRML', market_protection: '2' });
  await sleep(1500);
  const stop = await api('POST', '/api/accounts/acc-paper/algos', { code: 'TRAILING_STOP', instrument_id: FUT, side: 'SELL', qty: 75, params: { trail_paise: 20000 } });
  console.log('1. bought 75:', buy.status, '| trailing stop:', stop.status, stop.body && stop.body.algo_id);
  await sleep(1500);
  console.log('   bands while protected:', JSON.stringify((await bandsText()).replace(/\s+/g, ' ').trim()) || 'none');

  execSync(`kill -9 ${pid()}`);
  await sleep(1000);
  await startDemo();
  console.log('   after the restart:', await signIn());
  await page.locator('.bands', { hasText: 'UNPROTECTED' }).waitFor({ timeout: 15000 });
  const unprotected = (await bandsText()).replace(/\s+/g, ' ').trim();
  console.log('2. after SIGKILL and a restart:', unprotected);
  await page.screenshot({ path: `${IMAGES}/v1-2-unprotected-band.png` });

  const server = pid();
  execSync(`kill -STOP ${server}`);
  await sleep(9000);
  const frozen = (await bandsText()).replace(/\s+/g, ' ').trim();
  console.log('3. server frozen 9 s:', /PRICES \d+ s OLD/.test(frozen) ? frozen.match(/PRICES \d+ s OLD[^·]*/)[0] : `no stale band: ${frozen}`);
  await page.screenshot({ path: `${IMAGES}/v1-2-prices-old-band.png` });
  execSync(`kill -CONT ${server}`);
  await sleep(4000);
  console.log('   resumed: stale band gone:', !/PRICES \d+ s OLD/.test(await bandsText()));

  const sell = await api('POST', '/api/accounts/acc-paper/orders', { instrument_id: FUT, side: 'SELL', qty: 75, order_type: 'MARKET', product: 'NRML', market_protection: '2' });
  await page.waitForFunction(() => !(document.querySelector('.bands')?.textContent || '').includes('UNPROTECTED'), null, { timeout: 15000 }).catch(() => {});
  console.log('4. sold 75:', sell.status, '| UNPROTECTED band gone:', !(await bandsText()).includes('UNPROTECTED'));
  console.log('page errors:', errors.length ? errors : 'none');
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
