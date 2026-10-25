import { fakeRest } from "../test/fakes";
import { resolve, ThemeSetting, type ThemeEnv } from "./theme";

const PATH = "/api/me/settings/theme";

/** A computer whose light/dark setting the test controls, with its own <html> and storage. */
function fakeEnv(light = false, stored: string | null = null) {
  const root = document.createElement("html");
  const data = new Map<string, string>(stored === null ? [] : [["laddrix.theme", stored]]);
  let listener: (() => void) | null = null;
  const storage = { getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => void data.set(k, v) } as unknown as Storage;
  const env: ThemeEnv = {
    prefersLight: () => light,
    onSystemChange: (l) => ((listener = l), () => (listener = null)),
    root: () => root,
    storage: () => storage,
  };
  const flip = (to: boolean) => ((light = to), listener?.());
  return { env, root, data, flip };
}

function setup(saved: unknown = null, opts: { light?: boolean; stored?: string | null; put?: () => { status: number; body?: unknown } } = {}) {
  const server = fakeRest({ [`GET ${PATH}`]: { name: "theme", value: saved }, [`PUT ${PATH}`]: opts.put ?? (() => ({ status: 200, body: {} })) });
  const fake = fakeEnv(opts.light, opts.stored ?? null);
  return { ...server, ...fake, theme: new ThemeSetting(server.rest, fake.env) };
}

describe("theme", () => {
  it("'system' shows the computer's light or dark; a chosen theme shows itself", () => {
    expect(resolve("system", true)).toBe("light");
    expect(resolve("system", false)).toBe("dark");
    expect(resolve("cb", true)).toBe("cb");
    expect(resolve("dark", true)).toBe("dark");
  });

  it("before anything is chosen it follows the computer, live", () => {
    const { theme, root, flip } = setup(null, { light: true });
    expect(theme.getChoice()).toBe("system");
    expect(root.dataset.theme).toBe("light");
    const seen = vi.fn();
    theme.subscribe(seen);
    flip(false); // the computer switches to dark
    expect(root.dataset.theme).toBe("dark");
    expect(seen).toHaveBeenCalledTimes(1);
  });

  it("the first paint uses this browser's last theme, then the user's saved one from the server", async () => {
    const { theme, root, data } = setup({ name: "cb" }, { stored: "light" });
    expect(root.dataset.theme).toBe("light"); // before the server answers
    await theme.load();
    expect(theme.getChoice()).toBe("cb");
    expect(root.dataset.theme).toBe("cb");
    expect(data.get("laddrix.theme")).toBe("cb");
  });

  it("nothing (or nonsense) saved on the server: follow the computer", async () => {
    for (const saved of [null, { name: "purple" }, { name: 3 }, {}]) {
      const { theme, root } = setup(saved, { stored: "cb", light: true });
      await theme.load();
      expect(theme.getChoice()).toBe("system");
      expect(root.dataset.theme).toBe("light");
    }
    expect(setup(null, { stored: "purple" }).theme.getChoice()).toBe("system"); // a bad local copy is ignored
  });

  it("the server can't answer: keep what this browser showed last", async () => {
    const server = fakeRest({});
    const fake = fakeEnv(false, "light");
    const theme = new ThemeSetting(server.rest, fake.env);
    await theme.load();
    expect(theme.getChoice()).toBe("light");
  });

  it("choosing shows the theme at once and saves it for the user; 'system' saves nothing", async () => {
    const { theme, root, calls, flip } = setup();
    const seen = vi.fn();
    theme.subscribe(seen);
    const saving = theme.choose("light");
    expect(root.dataset.theme).toBe("light"); // before the server answers
    await saving;
    expect(calls.filter((c) => c.method === "PUT").map((c) => c.body)).toEqual([{ value: { name: "light" } }]);
    flip(false); // a chosen theme ignores the computer
    expect(root.dataset.theme).toBe("light");
    await theme.choose("system");
    expect(root.dataset.theme).toBe("dark");
    expect(calls.filter((c) => c.method === "PUT").at(-1)!.body).toEqual({ value: null });
    expect(seen).toHaveBeenCalled();
  });

  it("a refused save puts the old theme back and says why", async () => {
    const { theme, root, data } = setup(null, { stored: "dark", put: () => ({ status: 403, body: { detail: "not allowed" } }) });
    await expect(theme.choose("cb")).rejects.toThrow();
    expect(theme.getChoice()).toBe("dark");
    expect(root.dataset.theme).toBe("dark");
    expect(data.get("laddrix.theme")).toBe("dark");
  });

  it("blocked browser storage doesn't stop the theme", async () => {
    const server = fakeRest({ [`PUT ${PATH}`]: () => ({ status: 200, body: {} }) });
    const root = document.createElement("html");
    const broken = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("full"); } } as unknown as Storage; // prettier-ignore
    const theme = new ThemeSetting(server.rest, { prefersLight: () => false, onSystemChange: () => () => undefined, root: () => root, storage: () => broken });
    expect(root.dataset.theme).toBe("dark");
    await theme.choose("cb");
    expect(root.dataset.theme).toBe("cb");
  });
});
