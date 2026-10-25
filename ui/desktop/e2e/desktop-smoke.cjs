// Desktop check (run under a display, e.g. `xvfb-run -a node e2e/desktop-smoke.cjs`): starts
// the real app against the demo server, logs in, pops a ladder out into its own app window,
// checks it updates live and that pages can't reach Node, and prints memory per process.
//
// Needs: the demo server on http://localhost:8000 (see context/scripts/README.md), `npm run
// build` here, and Playwright (the cloud container has it at /opt/node22/lib/node_modules).
const path = require('node:path');
const { execSync } = require('node:child_process');
const { _electron: electron } = require(process.env.PLAYWRIGHT || '/opt/node22/lib/node_modules/playwright');

(async () => {
  const app = await electron.launch({
    executablePath: require('electron'),
    // a fresh profile each run: no session or saved window place left from the last one
    args: [path.join(__dirname, '..'), '--no-sandbox', '--server=http://localhost:8000',
      `--user-data-dir=${require('node:fs').mkdtempSync(path.join(require('node:os').tmpdir(), 'laddrix-desktop-'))}`],
  });
  const main = await app.firstWindow();
  await main.getByLabel('Username').fill('demo');
  await main.getByLabel('Password').fill('demo-password-123');
  // Codes work once (replay protection): wait for one this script hasn't used yet.
  const fs = require('node:fs');
  const os = require('node:os');
  const usedFile = path.join(os.tmpdir(), 'laddrix-desktop-smoke-code');
  const used = fs.existsSync(usedFile) ? fs.readFileSync(usedFile, 'utf8') : '';
  let code;
  for (;;) {
    code = execSync('python3 -m laddrix.tools.demo --code', { cwd: path.join(__dirname, '..', '..', '..') }).toString().trim();
    if (code !== used) break;
    await new Promise((r) => setTimeout(r, 2000));
  }
  fs.writeFileSync(usedFile, code);
  await main.getByLabel('Authenticator code').fill(code);
  await main.keyboard.press('Enter');
  // start from the default workspace: forget the saved layout (it may hold popped-out windows)
  await main.locator('.page-tabs').waitFor({ timeout: 20000 });
  console.log('windows restored from the saved layout:', app.windows().length);
  for (const w of app.windows()) if (w !== main) await w.close();
  await main.evaluate(async () => {
    for (const k of Object.keys(localStorage)) localStorage.removeItem(k);
    await fetch('/api/layouts/current', { method: 'DELETE', credentials: 'same-origin' });
  });
  await main.reload();
  try {
    await main.locator('.ladder .price.last').first().waitFor({ timeout: 20000 });
  } catch (err) {
    
    console.log('main window text:', (await main.locator('body').innerText()).slice(0, 300));
    throw err;
  }
  const probe = await main.evaluate(() => ({ require: typeof require, process: typeof process }));
  console.log('page can reach Node?', JSON.stringify(probe));

  const memory = async (label) => {
    const metrics = await app.evaluate(({ app: a }) => a.getAppMetrics().map((m) => ({ type: m.type, mb: Math.round(m.memory.workingSetSize / 1024) })));
    const total = metrics.reduce((n, m) => n + m.mb, 0);
    console.log(`${label}: ${total} MB in ${metrics.length} processes ${JSON.stringify(metrics)}`);
  };
  await main.waitForTimeout(2000);
  await memory('workspace open');

  const [popout] = await Promise.all([
    app.waitForEvent('window'),
    main.getByRole('button', { name: 'Pop out' }).nth(1).click(),
  ]);
  await popout.locator('.ladder .price.last').waitFor({ timeout: 10000 });
  const before = await popout.locator('.ladder .price.last').innerText();
  let after = before;
  for (let i = 0; i < 20 && after === before; i++) {
    await popout.waitForTimeout(500);
    after = await popout.locator('.ladder .price.last').innerText();
  }
  console.log('app windows:', app.windows().length, '| popped ladder live:', before, '->', after);
  const popProbe = await popout.evaluate(() => ({ require: typeof require, process: typeof process }));
  console.log('pop-out can reach Node?', JSON.stringify(popProbe));
  await memory('with one popped-out ladder');
  await popout.screenshot({ path: process.argv[2] ? path.join(process.argv[2], '8-desktop-popout.png') : 'popout.png' });
  await main.screenshot({ path: process.argv[2] ? path.join(process.argv[2], '9-desktop-main.png') : 'main.png' });
  await app.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
