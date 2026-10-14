const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch();
  for (const [w, scheme, name] of [[1600,'light','wide'],[1600,'dark','dark'],[420,'light','phone']]) {
    const p = await b.newPage({ viewport: { width: w, height: 1100 }, colorScheme: scheme });
    const errs = []; p.on('pageerror', e => errs.push(e.message)); p.on('console', m => { if (m.type()==='error') errs.push(m.text()); });
    await p.goto('file://' + process.argv[2]); await p.waitForTimeout(500);
    await p.screenshot({ path: `shot-${name}.png`, fullPage: true });
    console.log(name, errs, await p.evaluate(() => document.documentElement.scrollWidth));
  }
  await b.close();
})();
