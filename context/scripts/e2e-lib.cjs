// Shared steps for the browser checks on the demo (#448): sign in, and open a widget on a free
// page (the default since #411). A widget already on the page is used as it is; otherwise it
// is opened from the toolbar's Widgets ▾ menu. Each script then works inside the widget's own
// window (`section.frame`), so two widgets with the same buttons never get mixed up.
//
//   const { signIn, openWidget } = require('./e2e-lib.cjs');
//   await signIn(page);                       // the demo user, a fresh authenticator code
//   const reports = await openWidget(page, 'Reports');
//   await reports.getByRole('button', { name: 'History' }).click();
const { execFileSync } = require('child_process');
const path = require('path');

const ROOT = path.join(__dirname, '..', '..'); // the demo's data folder is found from here
const BASE = process.env.LADDRIX_URL || 'http://localhost:8000/';

/** The demo's current authenticator code (a new one every 30 s). */
function code() {
  return execFileSync('python3', ['-m', 'laddrix.tools.demo', '--code'], { cwd: ROOT }).toString().trim();
}

/** Sign in as `user` and wait until the page's widgets are drawn. */
async function signIn(page, { user = 'demo', password = 'demo-password-123', url = BASE } = {}) {
  await page.goto(url);
  await page.getByLabel('Username').fill(user);
  await page.getByLabel('Password').fill(password);
  await page.getByLabel('Authenticator code').fill(code());
  await page.keyboard.press('Enter');
  await page.locator('section.frame, .dv-tab').first().waitFor({ timeout: 20000 });
}

const quoted = (text) => text.replace(/["\\]/g, '\\$&');
const escaped = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** The window of the widget called `title` in Widgets ▾ ("Reports", "Order Book"…): the one
 * on the page, or a new one (`fresh: true` always opens a new one). A window whose title is
 * its contract (a ladder, a ticket, a chart) is found by the widget's own class: `has`. */
async function openWidget(page, title, { fresh = false, has = null } = {}) {
  const frames = has
    ? page.locator(`section.frame:has(${has})`)
    : page.locator(`section.frame[aria-label^="${quoted(title)}"]`);
  if (fresh || (await frames.count()) === 0) {
    const before = await frames.count();
    await page.getByRole('button', { name: 'Widgets ▾' }).click();
    await page.getByRole('menuitem', { name: new RegExp(`^${escaped(title)}`) }).click();
    await frames.nth(before).waitFor({ timeout: 10000 });
  }
  const frame = frames.last();
  await frame.scrollIntoViewIfNeeded();
  return frame;
}

module.exports = { signIn, openWidget, code, BASE };
