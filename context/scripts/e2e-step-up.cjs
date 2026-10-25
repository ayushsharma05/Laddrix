// The browser check of step-up and the session list (#363), from the repo root; ui/dist must be
// built. It runs its own demo in a fresh temporary folder on port 8766 (data/demo untouched):
//   1. signs in (a login counts as a step-up), turns the paper account off from the Admin
//      widget: never asked (turning off reduces risk);
//   2. makes the step-up 6 minutes old (as if the admin walked away), presses Turn on: the
//      "Confirm it's you" dialog asks; a wrong code is said; the right code turns it on;
//   3. a second browser signs in as the same user; Sessions lists both; the first ends the
//      second, whose next request is refused (401).
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { execSync, spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const REPO = path.resolve(__dirname, '../..');
const IMAGES = `${__dirname}/../images`;
const HOME = fs.mkdtempSync(path.join(os.tmpdir(), 'mocktrade-stepup-'));
const DATA = `${HOME}/data/demo`;
const BASE = 'http://localhost:8766';
const ENV = { ...process.env, PYTHONPATH: `${REPO}/src` };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const code = (offset = 0) => execSync(`python3 -c "import time; from mocktrade.server import security; print(security.totp_code(open('${DATA}/demo_totp.txt').read().strip(), time.time() + ${offset}))"`, { env: ENV }).toString().trim();
const freshWindow = async () => {
  const w = Math.floor(Date.now() / 30000);
  while (Math.floor(Date.now() / 30000) === w) await sleep(500);  // each code works once
};
(async () => {
  const demo = spawn('python3', ['-m', 'mocktrade.tools.demo', '--port', '8766', '--ui', `${REPO}/ui/dist`], { cwd: HOME, env: ENV, stdio: 'ignore' });
  for (let i = 0; i < 60; i++) {
    try { execSync('curl -sf http://127.0.0.1:8766/api/health >/dev/null'); break; } catch { await sleep(500); }
  }
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
  const results = [];
  const check = (ok, what) => { results.push(ok); console.log(`${ok ? 'PASS' : 'FAIL'} ${what}`); };
  try {
    const signIn = async (page) => {
      await freshWindow();
      await page.goto(`${BASE}/`);
      await page.getByLabel('Username').fill('demo');
      await page.getByLabel('Password').fill('demo-password-123');
      await page.getByLabel('Authenticator code').fill(code());
      await page.keyboard.press('Enter');
      await page.locator('section.frame').first().waitFor({ timeout: 20000 });
    };
    const admin = await browser.newPage({ viewport: { width: 1500, height: 950 } });
    const errors = [];
    admin.on('pageerror', (e) => errors.push(e.message));
    await signIn(admin);
    await admin.getByRole('button', { name: 'Widgets ▾' }).click();
    await admin.getByRole('menuitem', { name: /^Admin/ }).click();
    const box = admin.locator('.admin').last();
    await box.getByRole('button', { name: 'Accounts' }).click();
    const grid = box.getByRole('grid', { name: 'Accounts' });
    await grid.getByRole('button', { name: 'Turn off…' }).click();
    await grid.getByRole('button', { name: 'Confirm turn off' }).click();
    await grid.getByRole('button', { name: 'Turn on' }).waitFor({ timeout: 10000 });
    check(await admin.getByRole('dialog').count() === 0, '1. turning the account off asked for no code');

    // 6 minutes later: the login's step-up has run out
    execSync(`python3 -c "import sqlite3; db=sqlite3.connect('${DATA}/users.db'); db.execute(\\"UPDATE sessions SET stepped_up_at='2000-01-01T00:00:00+00:00'\\"); db.commit()"`);
    await grid.getByRole('button', { name: 'Turn on' }).click();
    const dialog = admin.getByRole('dialog', { name: "Confirm it's you" });
    await dialog.waitFor({ timeout: 10000 });
    check((await dialog.innerText()).includes("to change who trades an account or turn it on"), '2. "Confirm it\'s you" asks, saying why');
    await dialog.getByLabel('Authenticator code').fill('000000');
    await dialog.getByRole('button', { name: 'Confirm' }).click();
    await dialog.getByRole('alert').waitFor({ timeout: 10000 });
    check((await dialog.getByRole('alert').innerText()).startsWith('Wrong code'), '2. a wrong code is said');
    await admin.screenshot({ path: `${IMAGES}/h-363-step-up.png` });
    await freshWindow();
    await dialog.getByLabel('Authenticator code').fill(code());
    await dialog.getByRole('button', { name: 'Confirm' }).click();
    await grid.getByRole('button', { name: 'Turn off…' }).waitFor({ timeout: 10000 });
    check(await admin.getByRole('dialog').count() === 0, '2. the right code: the account is on again, the dialog gone');

    const laptop = await (await browser.newContext()).newPage();
    await signIn(laptop);
    await admin.getByRole('button', { name: 'Sessions' }).click();
    const list = admin.getByRole('dialog', { name: 'Your sessions' });
    await list.getByRole('row').nth(2).waitFor({ timeout: 10000 });
    const rows = await list.getByRole('row').count();
    check(rows === 3, `3. Sessions lists both logins (${rows - 1} rows)`);
    await admin.screenshot({ path: `${IMAGES}/h-363-sessions.png` });
    await list.getByRole('button', { name: 'End' }).click();
    await list.getByRole('button', { name: 'End' }).waitFor({ state: 'detached', timeout: 10000 });
    const status = await laptop.evaluate(async () => (await fetch('/api/me', { credentials: 'same-origin' })).status);
    check(status === 401, `3. the ended session is refused (${status})`);
    check(errors.length === 0, `no page errors (${errors.join('; ')})`);
  } finally {
    await browser.close();
    demo.kill('SIGINT');
    await sleep(2000);
    fs.rmSync(HOME, { recursive: true, force: true });
  }
  console.log(`${results.filter(Boolean).length}/${results.length} checks passed`);
  process.exit(results.every(Boolean) ? 0 : 1);
})();
