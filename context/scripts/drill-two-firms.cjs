// The two-firm drill (#397, #368d), from the repo root; ui/dist must be built. It runs its own
// demo in a fresh temporary folder on port 8768 (data/demo untouched):
//   1. the demo starts once (firm "main": the demo user and acc-paper), then stops;
//   2. the operator adds firm "acme" from the terminal, exactly as the runbook says
//      (users add-workspace acme ... --admin ravi; add-user tara --workspace acme;
//      add-account acc-acme paper ... --owner tara), and the demo starts again;
//   3. three browsers: demo (main's admin and trader), tara (acme's trader), ravi (acme's admin);
//   4. each sees only its own firm's accounts and users; tara's order is invisible to main and
//      main's id tried on acme's account is "not found"; acme's firm STOP shows on acme's screens
//      only, cancels acme's order, and main keeps trading.
// Screenshots: context/images/h-397-main.png, h-397-acme.png. Exit 1 if any check fails.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { execFileSync, execSync, spawn } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const REPO = path.resolve(__dirname, '../..');
const IMAGES = `${__dirname}/../images`;
const HOME = fs.mkdtempSync(path.join(os.tmpdir(), 'mocktrade-firms-'));
const DATA = `${HOME}/data/demo`;
const PORT = 8768;
const BASE = `http://localhost:${PORT}`;
const ENV = { ...process.env, PYTHONPATH: `${REPO}/src` };
const FUT = 'NFO:NIFTY26OCTFUT';
const PASSWORD = 'acme-password-123';
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const py = (code) => execFileSync('python3', ['-c', code], { env: ENV, cwd: HOME }).toString().trim();
const codeOf = (file) => py(`import time; from mocktrade.server import security; print(security.totp_code(open('${file}').read().strip(), time.time()))`);
const freshWindow = async () => {
  const w = Math.floor(Date.now() / 30000);
  while (Math.floor(Date.now() / 30000) === w) await sleep(500); // each code works once per user
};

async function startDemo() {
  const demo = spawn('python3', ['-m', 'mocktrade.tools.demo', '--port', String(PORT), '--ui', `${REPO}/ui/dist`], { cwd: HOME, env: ENV, stdio: 'ignore' });
  for (let i = 0; i < 60; i++) {
    try {
      execSync(`curl -sf http://127.0.0.1:${PORT}/api/health >/dev/null`);
      return demo;
    } catch {
      await sleep(500);
    }
  }
  throw new Error('the demo did not start');
}

async function stopDemo(demo) {
  const done = new Promise((r) => demo.on('exit', r));
  demo.kill('SIGINT');
  await done;
}

// the operator's commands, as in docs/runbooks/firms.md (the password typed is passed in)
function addAcme() {
  py(`
import contextlib, io, re
from mocktrade.tools import users
env = {"MOCKTRADE_SECRET_KEY": open("data/demo/secret.key").read().strip(), "MOCKTRADE_USERS_DB": "data/demo/users.db"}
def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert users.main(list(argv), env, password=lambda: "${PASSWORD}") == 0
    return out.getvalue()
for who, text in (("ravi", run("add-workspace", "acme", "Acme Capital", "--admin", "ravi")),
                  ("tara", run("add-user", "tara", "Tara", "--roles", "trader", "--workspace", "acme"))):
    open(f"data/demo/{who}_totp.txt", "w").write(re.search(r"secret=([A-Z2-7]+)", text).group(1))
run("add-account", "acc-acme", "paper", "PAPER2", "--owner", "tara")
`);
}

(async () => {
  let demo = await startDemo();
  await stopDemo(demo);
  addAcme();
  demo = await startDemo();
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
  const results = [];
  const check = (ok, what) => {
    results.push(ok);
    console.log(`${ok ? 'PASS' : 'FAIL'} ${what}`);
  };
  try {
    const signIn = async (user, password, totpFile) => {
      const page = await (await browser.newContext({ viewport: { width: 1500, height: 950 } })).newPage();
      page.errors = [];
      page.on('pageerror', (e) => page.errors.push(e.message));
      await page.goto(`${BASE}/`);
      await page.getByLabel('Username').fill(user);
      await page.getByLabel('Password').fill(password);
      await page.getByLabel('Authenticator code').fill(codeOf(totpFile));
      await page.keyboard.press('Enter');
      await page.locator('.dv-tab').first().waitFor({ timeout: 20000 });
      return page;
    };
    const api = (page) => ({
      get: async (p) => {
        const r = await page.request.get(`${BASE}${p}`);
        return { status: r.status(), body: r.ok() ? await r.json() : null };
      },
      post: async (p, data) => {
        const r = await page.request.post(`${BASE}${p}`, { data, headers: { Origin: BASE } });
        return { status: r.status(), body: await r.json().catch(() => null) };
      },
    });
    const main = await signIn('demo', 'demo-password-123', `${DATA}/demo_totp.txt`);
    const tara = await signIn('tara', PASSWORD, `${DATA}/tara_totp.txt`);
    const ravi = await signIn('ravi', PASSWORD, `${DATA}/ravi_totp.txt`);
    const [M, T, R] = [api(main), api(tara), api(ravi)];

    // 1. each firm sees only its own accounts and users
    const accounts = async (a) => (await a.get('/api/me')).body.accounts.map((x) => x.id).sort().join(',');
    check((await accounts(M)) === 'acc-paper', `1. main sees only its own account (${await accounts(M)})`);
    check((await accounts(T)) === 'acc-acme', `1. acme's trader sees only acme's account (${await accounts(T)})`);
    const people = async (a) => (await a.get('/api/admin/users')).body.map((u) => u.id).sort().join(',');
    check((await people(R)) === 'ravi,tara', `1. acme's admin lists only acme's users (${await people(R)})`);
    check(!(await people(M)).includes('ravi') && !(await people(M)).includes('tara'), `1. main's admin lists none of acme's (${await people(M)})`);

    // 2. an acme order is acme's alone
    const order = { instrument_id: FUT, side: 'BUY', qty: 75, order_type: 'LIMIT', product: 'NRML', price_paise: 2400000 };
    const placed = await T.post('/api/accounts/acc-acme/orders', order);
    check(placed.status === 201 && placed.body.status !== 'REJECTED', `2. tara's order rests at acme (${placed.status} ${placed.body && placed.body.status} ${placed.body && placed.body.reason})`);
    check((await M.get('/api/accounts/acc-acme/orders')).status === 404, "2. main asking for acme's orders: not found");
    check((await M.get('/api/accounts/acc-paper/orders')).body.every((o) => o.request.client_order_id !== placed.body.request.client_order_id), "2. acme's order is not in main's book");
    check((await T.get('/api/accounts/acc-paper/orders')).status === 404, "2. acme asking for main's orders: not found");

    // 3. acme's admin opens the Admin screen: acme's people only
    await ravi.getByRole('button', { name: 'Widgets ▾' }).click();
    await ravi.getByRole('menuitem', { name: /^Admin/ }).click();
    const users = ravi.locator('.admin').last().getByRole('grid', { name: 'Users' });
    await users.getByText('tara').first().waitFor({ timeout: 10000 });
    check(!(await users.innerText()).includes('demo'), "3. acme's Admin screen shows no main user");

    // 4. acme's firm STOP: acme's screens only; acme's order cancelled; main keeps trading
    const stop = await R.post('/api/firm/stop', { reason: 'two-firm drill' });
    check(stop.status === 200, `4. acme's admin stops acme (${stop.status})`);
    await tara.getByText('ALL TRADING STOPPED (firm)').first().waitFor({ timeout: 10000 }).catch(() => {});
    check(await tara.getByText('ALL TRADING STOPPED (firm)').count() > 0, "4. acme's trader sees the firm STOP band");
    await sleep(1500);
    check(await main.getByText('ALL TRADING STOPPED (firm)').count() === 0, '4. main sees no STOP band');
    const acmeOrder = (await T.get('/api/accounts/acc-acme/orders')).body.find((o) => o.request.client_order_id === placed.body.request.client_order_id);
    check(acmeOrder && acmeOrder.status === 'CANCELLED', `4. acme's resting order was cancelled (${acmeOrder && acmeOrder.status})`);
    const refused = await T.post('/api/accounts/acc-acme/orders', { ...order, price_paise: 2400010 });
    check(refused.body && refused.body.status === 'REJECTED', `4. acme can't trade while stopped (${refused.body && refused.body.reason})`);
    const mainOrder = await M.post('/api/accounts/acc-paper/orders', { ...order, price_paise: 2400020 });
    check(mainOrder.status === 201 && mainOrder.body.status !== 'REJECTED', `4. main still trades (${mainOrder.body && mainOrder.body.status})`);

    await main.screenshot({ path: `${IMAGES}/h-397-main.png` });
    await tara.screenshot({ path: `${IMAGES}/h-397-acme.png` });
    const errors = [...main.errors, ...tara.errors, ...ravi.errors];
    check(errors.length === 0, `5. no page errors (${errors.join('; ')})`);
  } finally {
    await browser.close();
    await stopDemo(demo);
    fs.rmSync(HOME, { recursive: true, force: true });
  }
  const failed = results.filter((x) => !x).length;
  console.log(failed ? `${failed} FAILED` : `all ${results.length} passed`);
  process.exit(failed ? 1 : 0);
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
