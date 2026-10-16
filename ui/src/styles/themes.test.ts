// K13.5: every colour is a token in theme.css, every theme sets them, and the text people read
// meets WCAG AA contrast (4.5:1) on the surface it sits on, in every theme.
import { readFileSync } from "node:fs";

// read as text from disk, next to this file (the test setup turns CSS imports off)
const here = decodeURIComponent(new URL(import.meta.url).pathname).replace(/[^/]+$/, "");
const css = (name: string) => readFileSync(here + name, "utf8");
const THEME = css("theme.css");
const SHEETS: Record<string, string> = { "shell.css": css("shell.css"), "widgets.css": css("widgets.css") };

/** The tokens one block sets: `:root`, or `:root[data-theme="light"]`. */
function block(selector: string): Record<string, string> {
  const start = THEME.indexOf(`${selector} {`);
  if (start < 0) throw new Error(`no ${selector} block`);
  const body = THEME.slice(start, THEME.indexOf("\n}", start));
  return Object.fromEntries([...body.matchAll(/^\s*(--[\w-]+):\s*([^;]+);/gm)].map((m) => [m[1]!, m[2]!.trim()]));
}

const BASE = block(":root");
export const THEMES: Record<string, Record<string, string>> = {
  dark: BASE,
  light: { ...BASE, ...block(':root[data-theme="light"]') },
  cb: { ...BASE, ...block(':root[data-theme="cb"]') },
};

function luminance(hex: string): number {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? [...h].map((c) => c + c).join("") : h.slice(0, 6);
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi! + 0.05) / (lo! + 0.05);
}

// what people read, on what it sits (each pair found in the stylesheets)
const SURFACES = ["--body", "--desktop", "--toolbar", "--chrome", "--zebra", "--well", "--menu", "--panel-fill", "--row-hover", "--tab-active", "--strike-fill", "--selected-fill"];
const PAIRS: [string, string][] = [
  ...SURFACES.map((bg) => ["--text", bg] as [string, string]),
  ...["--body", "--desktop", "--toolbar", "--chrome", "--zebra", "--well", "--panel-fill"].map((bg) => ["--text-2", bg] as [string, string]),
  ...["--body", "--chrome", "--well"].map((bg) => ["--text-3", bg] as [string, string]),
  ["--chrome-ink", "--menu"], ["--chrome-ink", "--toolbar"], ["--chrome-ink", "--tab-inactive"],
  ...["--good", "--bad", "--pending", "--buy-text", "--sell-text", "--sell-soft-ink"].flatMap((fg) => [[fg, "--body"], [fg, "--well"]] as [string, string][]),
  ...["--bid", "--ask", "--bid-empty", "--ask-empty", "--work-buy", "--work-sell", "--body"].map((bg) => ["--ladder-ink", bg] as [string, string]),
  ...["--primary", "--last", "--stop-now-fill", "--buy-strong", "--sell-strong"].map((bg) => ["--on-strong", bg] as [string, string]),
  ["--price-text", "--price"], ["--bad-ink", "--bad-fill"], ["--bad-ink", "--stop-bg"], ["--warn-ink", "--warn-fill"], ["--warn-strong-ink", "--warn-fill"],
  ["--live-ink", "--live-strip"], ["--warn-ink", "--paper-strip"], ["--login-ink", "--login-fill"], ["--stopped-ink", "--stopped-band"],
  ["--stopped-ink", "--firm-stopped-fill"], ["--replay-ink", "--replay-fill"], ["--replay-dim", "--replay-fill"], ["--atm-ink", "--atm-fill"],
]; // prettier-ignore

describe("themes", () => {
  it.each(Object.keys(THEMES))("%s: text meets WCAG AA (4.5:1) on its surface", (name) => {
    const t = THEMES[name]!;
    const fails = PAIRS.filter(([fg, bg]) => contrast(t[fg]!, t[bg]!) < 4.5).map(([fg, bg]) => `${fg} on ${bg}: ${contrast(t[fg]!, t[bg]!).toFixed(2)}`);
    expect(fails).toEqual([]);
  });

  it("the light theme sets every colour (nothing dark leaks in); the colour-blind one only known tokens", () => {
    const colours = Object.keys(BASE).filter((k) => /^(#|rgba?\()/.test(BASE[k]!));
    const light = block(':root[data-theme="light"]');
    expect(colours.filter((k) => !(k in light))).toEqual([]);
    for (const k of Object.keys(block(':root[data-theme="cb"]'))) expect(BASE).toHaveProperty(k);
    for (const k of Object.keys(light)) if (k !== "color-scheme") expect(BASE).toHaveProperty(k);
  });

  it("the colour-blind theme tells buy from sell and profit from loss without red against green", () => {
    const t = THEMES.cb!;
    const hue = (hex: string) => {
      const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
      const max = Math.max(r!, g!, b!), min = Math.min(r!, g!, b!), d = max - min;
      if (!d) return 0;
      const h = max === r ? ((g! - b!) / d) % 6 : max === g ? (b! - r!) / d + 2 : (r! - g!) / d + 4;
      return (h * 60 + 360) % 360;
    };
    const blueish = (c: string) => hue(c) >= 190 && hue(c) <= 250;
    const orangeish = (c: string) => hue(c) >= 20 && hue(c) <= 45;
    for (const k of ["--good", "--bid", "--buy-text", "--work-buy"]) expect(blueish(t[k]!), k).toBe(true);
    for (const k of ["--bad", "--ask", "--sell-text", "--work-sell"]) expect(orangeish(t[k]!), k).toBe(true);
    // the last traded price sits between the bids and the asks: neither blue nor orange
    expect(blueish(t["--last"]!) || orangeish(t["--last"]!)).toBe(false);
  });

  it("no colour is written anywhere but in theme.css's token blocks", () => {
    const literal = /#[0-9a-fA-F]{3,8}\b|rgba?\(/;
    for (const [name, text] of Object.entries(SHEETS)) {
      const lines = text.split("\n").filter((l) => literal.test(l) && !l.trim().startsWith("/*"));
      expect(lines, name).toEqual([]);
    }
    const outside = THEME.split(/\n}\n/).filter((b) => !b.includes(":root")).join("\n");
    expect(outside.split("\n").filter((l) => literal.test(l) && !l.trim().startsWith("/*"))).toEqual([]);
  });
});
