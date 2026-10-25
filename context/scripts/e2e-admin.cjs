// Real browser check of K13.2 on the demo (#282), with the demo up and ui/dist rebuilt:
//   1. the demo admin opens Admin from Widgets, adds a trader: the one-time password and the
//      authenticator key are shown once, with a QR code;
//   2. that user signs in with them in a second browser and sees only "Set your password";
//      a too-short password gets the server's words; their own one opens the terminal;
//   3. the admin tightens acc-paper's per-order limit to 1 lot in the Limits tab (check, then
//      save) and a 2-lot order is refused with that limit; then puts it back (looser: two clicks).
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const { execSync } = require('child_process');
const flat = (s) => (s || '').replace(/\s+/g, ' ').trim();
const totp = (secret) => execSync(`python3 -c "import pyotp,sys; print(pyotp.TOTP(sys.argv[1]).now())" ${secret}`).toString().trim();
const shots = '/tmp/claude-0';

(async () => {
  const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || undefined });
  const errors = [];
  const admin = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  admin.on('pageerror', (e) => errors.push(e.message));
  await admin.goto('http://localhost:8000/');
  await admin.getByLabel('Username').fill('demo');
  await admin.getByLabel('Password').fill('demo-password-123');
  await admin.getByLabel('Authenticator code').fill(execSync('python3 -m laddrix.tools.demo --code').toString().trim());
  await admin.keyboard.press('Enter');
  await admin.locator('section.frame').first().waitFor({ timeout: 20000 });
  const api = (page, m, p, b) => page.evaluate(async ([m, p, b]) => {
    const r = await fetch(p, { method: m, credentials: 'same-origin', headers: { 'content-type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
    return { status: r.status, body: r.status === 204 ? null : await r.json() };
  }, [m, p, b]);

  // 1. a new user from the Admin widget
  await admin.getByRole('button', { name: 'Widgets ▾' }).click();
  await admin.getByRole('menuitem', { name: /^Admin/ }).click();
  const box = admin.locator('.admin').last();
  await box.getByRole('grid', { name: 'Users' }).getByRole('row').first().waitFor({ timeout: 10000 });
  console.log('users:', (await box.getByRole('grid', { name: 'Users' }).getByRole('row').allTextContents()).map(flat).join(' | '));
  const uid = `e2e${Date.now() % 100000}`;
  await box.getByRole('button', { name: '+ New user' }).click();
  await box.getByLabel('User id').fill(uid);
  await box.getByLabel('Name').fill('E2E Trader');
  await box.getByRole('button', { name: 'Add user' }).click();
  const once = box.getByRole('region', { name: 'Shown once' });
  await once.waitFor({ timeout: 5000 });
  const temp = flat(await once.getByLabel('One-time password').textContent());
  const key = flat(await once.getByLabel('Authenticator key').textContent()).replace(/ /g, '');
  console.log('shown once: password', temp.length, 'chars | key', key.length, 'chars | QR:', await once.getByRole('img', { name: 'Authenticator QR code' }).count());
  await admin.screenshot({ path: `${shots}/e2e-admin-users.png` });
  await once.getByRole('button', { name: "Done: I've handed these over" }).click();
  console.log('after Done, still shown:', await box.getByRole('region', { name: 'Shown once' }).count());

  // 2. their first sign-in, in a second browser
  const ctx = await browser.newContext({ viewport: { width: 1200, height: 900 } });
  const them = await ctx.newPage();
  them.on('pageerror', (e) => errors.push(e.message));
  await them.goto('http://localhost:8000/');
  await them.getByLabel('Username').fill(uid);
  await them.getByLabel('Password').fill(temp);
  await them.getByLabel('Authenticator code').fill(totp(key));
  await them.keyboard.press('Enter');
  await them.getByText('Laddrix · Set your password').waitFor({ timeout: 10000 });
  console.log('their page:', flat(await them.locator('.window-bar').textContent()), '| terminal shown:', await them.getByRole('button', { name: 'Widgets ▾' }).count());
  await them.getByLabel('One-time password').fill(temp);
  await them.getByLabel('New password', { exact: true }).fill('short');
  await them.getByLabel('New password again').fill('short');
  await them.getByRole('button', { name: 'Set password' }).click();
  console.log('too short:', flat(await them.getByRole('alert').textContent()));
  await them.getByLabel('New password', { exact: true }).fill('e2e trader own password');
  await them.getByLabel('New password again').fill('e2e trader own password');
  await them.getByRole('button', { name: 'Set password' }).click();
  await them.getByRole('button', { name: 'Widgets ▾' }).waitFor({ timeout: 15000 });
  console.log('their terminal opened; Admin offered to them:', await (async () => {
    await them.getByRole('button', { name: 'Widgets ▾' }).click();
    const n = await them.getByRole('menuitem', { name: /^Admin/ }).count();
    await them.keyboard.press('Escape');
    return n;
  })());
  await them.screenshot({ path: `${shots}/e2e-admin-their-terminal.png` });

  // 3. a tighter limit, met by the next order
  await box.getByRole('button', { name: 'Limits', exact: true }).first().click();
  const form = box.getByRole('group', { name: /^Limits of / });
  await form.waitFor({ timeout: 5000 });
  const account = (await form.getAttribute('aria-label')).replace('Limits of ', '');
  await box.getByLabel('Most lots per order').fill('1');
  await box.getByRole('button', { name: 'Check changes' }).click();
  const what = box.getByRole('region', { name: 'What changes' });
  await what.waitFor({ timeout: 5000 });
  console.log('check:', flat(await what.textContent()));
  await box.getByRole('button', { name: 'Save', exact: true }).click();
  console.log('saved:', flat(await box.getByRole('status').textContent()));
  await admin.screenshot({ path: `${shots}/e2e-admin-limits.png` });
  const inst = (await api(admin, 'GET', '/api/instruments/NFO:NIFTY26OCTFUT')).body;
  const order = { instrument_id: inst.id, side: 'BUY', qty: inst.lot_size * 2, order_type: 'LIMIT', product: 'NRML', price_paise: (Math.floor(inst.ltp_paise / inst.tick_paise) - 300) * inst.tick_paise };
  const two = (await api(admin, 'POST', `/api/accounts/${account}/orders`, order)).body;
  console.log(`a 2-lot order on ${account}:`, two.status, '|', two.reason);
  await box.getByLabel('Most lots per order').fill('10');
  await box.getByRole('button', { name: 'Check changes' }).click();
  await what.waitFor({ timeout: 5000 });
  console.log('back to 10:', flat(await what.textContent()));
  await box.getByRole('button', { name: /^Save… \(1 looser\)$/ }).click();
  await box.getByRole('button', { name: 'Confirm: loosen and save' }).click();
  console.log('saved:', flat(await box.getByRole('status').textContent()));
  console.log('page errors:', errors.length ? errors : 'none');
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
