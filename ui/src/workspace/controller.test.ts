import type { DockviewApi } from "dockview-react";
import { fakeRest } from "../test/fakes";
import { WorkspaceController, uniqueName } from "./controller";
import type { CanvasWindow } from "./canvas";
import { CURRENT, LayoutStore, type PageMode, type WorkspaceDoc } from "./layouts";
import { registerWidget } from "./registry";

registerWidget({
  kind: "probe",
  title: "Probe",
  description: "test widget",
  component: () => null,
  defaults: () => ({ link: "none" as const, n: 1 }),
  link: "none",
});

/** Enough of dockview to test the controller: a list of panels that toJSON/fromJSON copy. */
function fakeDock() {
  let panels: { id: string; component: string; params: unknown }[] = [];
  let layoutListener: (() => void) | null = null;
  const api = {
    toJSON: () => ({ panels: structuredClone(panels) }),
    fromJSON: (data: { panels: typeof panels; broken?: boolean }) => {
      if (data.broken) throw new Error("unknown component");
      panels = structuredClone(data.panels);
    },
    clear: () => {
      panels = [];
    },
    added: [] as Record<string, unknown>[],
    // where each tile is on screen (none: hidden, as in a test without layout)
    rects: new Map<string, { left: number; top: number; width: number; height: number }>(),
    // tabs: panels sharing a group (by default each panel is alone in its own), and the one showing
    tabbed: new Map<string, string>(),
    showing: new Map<string, string>(),
    groups: new Map<string, object>(),
    get panels() {
      return panels.map((p) => {
        const key = api.tabbed.get(p.id) ?? p.id;
        if (!api.groups.has(key)) {
          api.groups.set(key, {
            api: { location: { type: "grid" } },
            element: { getBoundingClientRect: () => api.rects.get(key) ?? { left: 0, top: 0, width: 0, height: 0 } },
            get activePanel() {
              return { id: api.showing.get(key) ?? p.id };
            },
          });
        }
        return { id: p.id, params: p.params, api: { component: p.component }, group: api.groups.get(key) };
      });
    },
    addPanel: (o: { id: string; component: string; params: unknown }) => {
      api.added.push(o);
      panels.push({ id: o.id, component: o.component, params: o.params });
      layoutListener?.();
      return { id: o.id };
    },
    get activePanel() {
      return panels.length ? { id: panels[panels.length - 1]!.id } : undefined;
    },
    onDidLayoutChange: (fn: () => void) => {
      layoutListener = fn;
      return { dispose: () => {} };
    },
    // pop-out windows: which panels are out, and whether the browser allows the window
    popoutAllowed: true,
    popped: new Map<string, { closed: boolean }>(),
    popoutCalls: [] as { id: string; url: string | undefined; size: number[] | null }[],
    getPanel(id: string) {
      const found = panels.find((p) => p.id === id);
      if (!found) return undefined;
      const win = api.popped.get(id);
      return {
        id,
        api: { component: found.component },
        params: found.params,
        group: {
          api: {
            location: win
              ? { type: "popout", getWindow: () => ({ close: () => { win.closed = true; api.popped.delete(id); } }) }
              : { type: "grid" },
          },
        },
      };
    },
    async addPopoutGroup(panel: { id: string }, options?: { popoutUrl?: string; position?: { width: number; height: number } }) {
      api.popoutCalls.push({ id: panel.id, url: options?.popoutUrl, size: options?.position ? [options.position.width, options.position.height] : null });
      if (!api.popoutAllowed) return false;
      api.popped.set(panel.id, { closed: false });
      layoutListener?.();
      return true;
    },
  };
  return { api: api as unknown as DockviewApi, raw: api, panels: () => panels, move: () => layoutListener?.() };
}

function setup(saved?: WorkspaceDoc, mode: PageMode = "docked") {
  const kept = new Map<string, unknown>();
  if (saved) kept.set(CURRENT, saved);
  const server = fakeRest({});
  for (const name of [CURRENT, "Morning"]) {
    server.routes[`GET /api/layouts/${name}`] = () =>
      kept.has(name) ? { status: 200, body: { name, layout: kept.get(name) } } : { status: 404, body: { detail: "no" } };
    server.routes[`PUT /api/layouts/${name}`] = (body: unknown) => {
      kept.set(name, (body as { layout: unknown }).layout);
      return { status: 200, body: { name } };
    };
  }
  const timers: (() => void)[] = [];
  const notes: string[] = [];
  const built: string[] = [];
  const opened: { url: string; box: { width: number; height: number }; closed: boolean; close(): void }[] = [];
  let allowPopups = true;
  const controller = new WorkspaceController({
    newPageMode: mode,
    buildDefaultCanvas: (page) => {
      built.push(`${page} (free)`);
      return page === "Main" ? [win("probe-main", 8, 8, 400, 300)] : [];
    },
    openWindow: (url, box) => {
      if (!allowPopups) return null;
      const w = { url, box, closed: false, close: () => void (w.closed = true) };
      opened.push(w);
      return w as unknown as Window;
    },
    layouts: new LayoutStore(server.rest, null, "ayush"),
    buildDefault: (api, page) => {
      built.push(page);
      if (page === "Main") api.addPanel({ id: "probe-main", component: "probe", params: { link: "none", n: 1 } });
    },
    notify: (level, text) => notes.push(`${level} ${text}`),
    setTimer: (fn) => {
      timers.push(fn);
      return timers.length as unknown as ReturnType<typeof setTimeout>;
    },
    clearTimer: () => {},
  });
  const flush = async () => {
    const pending = timers.splice(0);
    pending.forEach((fn) => fn());
    await new Promise((r) => setTimeout(r, 0));
  };
  const blockPopups = () => void (allowPopups = false);
  return { controller, kept, timers, notes, built, flush, opened, blockPopups };
}

function win(id: string, x: number, y: number, w: number, h: number, z = 1): CanvasWindow {
  return { id, kind: "probe", params: { link: "none", n: 1 }, x, y, w, h, z };
}

describe("WorkspaceController", () => {
  it("pops a widget out into its own window with the same settings, and docks it back", async () => {
    const { controller, notes } = setup();
    const dock = fakeDock();
    await controller.start(dock.api);
    controller.addWidget("probe", { n: 5 });
    const id = dock.panels()[1]!.id;
    expect(await controller.popOut(id)).toBe(true);
    expect(dock.raw.popoutCalls).toEqual([{ id, url: "/popout.html", size: [360, 640] }]); // never too small to use
    expect(dock.panels().find((p) => p.id === id)!.params).toMatchObject({ n: 5 }); // props kept
    expect(controller.saveState).toBe("unsaved"); // the window is part of the saved layout
    controller.dockBack(id);
    expect(dock.raw.popped.has(id)).toBe(false);
    expect(dock.panels().find((p) => p.id === id)!.params).toMatchObject({ n: 5 });
    expect(notes).toEqual([]);
  });

  it("says so when the browser blocks the window, and not while the layout is locked", async () => {
    const { controller, notes } = setup();
    const dock = fakeDock();
    await controller.start(dock.api);
    dock.raw.popoutAllowed = false;
    expect(await controller.popOut("probe-main")).toBe(false);
    expect(notes.pop()).toContain("allow pop-ups");
    controller.setLocked(true);
    expect(await controller.popOut("probe-main")).toBe(false);
    expect(dock.raw.popoutCalls).toHaveLength(1);
    expect(await controller.popOut("nope")).toBe(false);
  });

  it("builds the starter page the first time and autosaves after changes", async () => {
    const { controller, kept, built, flush } = setup();
    const dock = fakeDock();
    await controller.start(dock.api);
    expect(built).toEqual(["Main"]);
    expect(dock.panels().map((p) => p.id)).toEqual(["probe-main"]);
    expect(kept.has(CURRENT)).toBe(false); // building the default is not a user change

    controller.addWidget("probe", { n: 2 });
    expect(controller.saveState).toBe("unsaved");
    await flush();
    expect(controller.saveState).toBe("saved");
    const doc = kept.get(CURRENT) as WorkspaceDoc;
    expect((doc.pages[0]!.dock as { panels: { params: { n: number } }[] }).panels.map((p) => p.params.n)).toEqual([1, 2]);
  });

  it("restores the saved workspace exactly (save → reload → same layout)", async () => {
    const first = setup();
    const dock1 = fakeDock();
    await first.controller.start(dock1.api);
    first.controller.addWidget("probe", { n: 7 });
    await first.controller.addPage("Options");
    first.controller.addWidget("probe", { n: 9 });
    await first.flush();
    const saved = first.kept.get(CURRENT) as WorkspaceDoc;

    // a reload: a new controller and a fresh dockview
    const second = setup(saved);
    const dock2 = fakeDock();
    await second.controller.start(dock2.api);
    expect(second.built).toEqual([]);
    expect(second.controller.doc.pages.map((p) => p.name)).toEqual(["Main", "Options"]);
    expect(second.controller.doc.active).toBe(1);
    expect(dock2.panels().map((p) => (p.params as { n: number }).n)).toEqual([9]);
    await second.controller.switchTo(0);
    expect(dock2.panels().map((p) => (p.params as { n: number }).n)).toEqual([1, 7]);
  });

  it("keeps each page's widgets when switching pages", async () => {
    const { controller } = setup();
    const dock = fakeDock();
    await controller.start(dock.api);
    await controller.addPage();
    expect(controller.doc.pages.map((p) => p.name)).toEqual(["Main", "Page"]);
    expect(dock.panels()).toEqual([]); // new pages start empty
    controller.addWidget("probe", { n: 3 });
    await controller.switchTo(0);
    expect(dock.panels().map((p) => p.id)).toEqual(["probe-main"]);
    await controller.switchTo(1);
    expect(dock.panels().map((p) => (p.params as { n: number }).n)).toEqual([3]);
  });

  it("renames and removes pages", async () => {
    const { controller } = setup();
    await controller.start(fakeDock().api);
    await controller.addPage("Options");
    await controller.addPage("Options");
    expect(controller.doc.pages.map((p) => p.name)).toEqual(["Main", "Options", "Options 2"]);
    controller.renamePage(2, "  Spreads  ");
    controller.renamePage(1, "Spreads");
    expect(controller.doc.pages.map((p) => p.name)).toEqual(["Main", "Spreads 2", "Spreads"]);
    await controller.removePage(2); // the active one
    expect(controller.doc.pages.map((p) => p.name)).toEqual(["Main", "Spreads 2"]);
    expect(controller.doc.active).toBe(1);
    await controller.removePage(0);
    await controller.removePage(0); // the last page can't go
    expect(controller.doc.pages.map((p) => p.name)).toEqual(["Spreads 2"]);
  });

  it("resets a page it can't open, and says so", async () => {
    const broken: WorkspaceDoc = { version: 1, pages: [{ name: "Main", dock: { broken: true, panels: [] } }], active: 0 };
    const { controller, notes, built } = setup(broken);
    const dock = fakeDock();
    await controller.start(dock.api);
    expect(built).toEqual(["Main"]);
    expect(notes[0]).toMatch(/couldn't be opened/);
  });

  it("saves once after a restore, not for every widget it puts back", async () => {
    const { controller, timers } = setup();
    await controller.start(fakeDock().api);
    timers.length = 0;
    await controller.reset(); // rebuilding adds panels, which fire layout changes
    expect(timers).toHaveLength(1);
  });

  it("doesn't add widgets while locked", async () => {
    const { controller, notes } = setup();
    const dock = fakeDock();
    await controller.start(dock.api);
    controller.setLocked(true);
    controller.addWidget("probe");
    expect(dock.panels()).toHaveLength(1);
    expect(notes[0]).toMatch(/locked/);
  });

  it("saves and opens named layouts", async () => {
    const { controller, kept, notes } = setup();
    const dock = fakeDock();
    await controller.start(dock.api);
    controller.addWidget("probe", { n: 5 });
    await expect(controller.saveAs("Morning")).resolves.toBe(true);
    expect(kept.has("Morning")).toBe(true);
    await expect(controller.saveAs("  ")).resolves.toBe(false);
    await controller.reset();
    expect(dock.panels()).toHaveLength(1);
    await controller.open("Morning");
    expect(dock.panels()).toHaveLength(2);
    expect(notes.at(-1)).toBe('OK Layout "Morning" opened');
  });
});

describe("free pages (#410)", () => {
  it("starts a first-run Main page as free windows, and new pages free and empty", async () => {
    const { controller, built, kept } = setup(undefined, "free");
    const dock = fakeDock();
    await controller.start(dock.api);
    expect(built).toEqual(["Main (free)"]);
    expect(controller.mode).toBe("free");
    expect(controller.canvas?.windows.map((w) => w.id)).toEqual(["probe-main"]);
    expect(dock.panels()).toEqual([]); // dockview stays empty behind a free page
    expect(kept.has(CURRENT)).toBe(false); // the starter windows are not a user change
    await controller.addPage("Scalping");
    expect(controller.doc.pages[1]!.mode).toBe("free");
    expect(controller.canvas?.windows).toEqual([]);
  });

  it("moves, resizes, raises and closes windows, and saves each change", async () => {
    const { controller, kept, flush } = setup(undefined, "free");
    await controller.start(fakeDock().api);
    controller.addWidget("probe", { n: 2 });
    const [a, b] = controller.canvas!.windows;
    expect([b!.x, b!.y]).toEqual([a!.x, a!.y + a!.h + 4]); // below the window in front (a grid-like widget), a 4 px gap
    expect(b!.z).toBeGreaterThan(a!.z);
    expect(controller.moveWindow(a!.id, { x: 3000, y: 2000, w: 500, h: 250 })).toBe(true); // past the screen: fine
    expect(controller.moveWindow(a!.id, { x: 3000, y: 2000, w: 500, h: 250 })).toBe(false); // nothing changed
    controller.focusWindow(a!.id);
    expect(controller.canvas!.windows.find((w) => w.id === a!.id)!.z).toBe(2);
    await flush();
    const saved = kept.get(CURRENT) as WorkspaceDoc;
    expect(saved.pages[0]).toMatchObject({ name: "Main", mode: "free", dock: null });
    expect(saved.pages[0]!.canvas!.windows.find((w) => w.id === a!.id)).toMatchObject({ x: 3000, y: 2000, w: 500, h: 250, z: 2 });
    controller.closeWindow(b!.id);
    expect(controller.canvas!.windows.map((w) => w.id)).toEqual([a!.id]);
  });

  it("restores free windows exactly after a reload, next to docked pages", async () => {
    const first = setup(undefined, "free");
    await first.controller.start(fakeDock().api);
    first.controller.moveWindow("probe-main", { x: 40, y: 1200, w: 640, h: 200 });
    first.controller.setWindowParams("probe-main", { link: "pink", n: 7 });
    await first.controller.addPage("Tiles", "docked");
    first.controller.addWidget("probe", { n: 9 });
    await first.flush();
    const saved = first.kept.get(CURRENT) as WorkspaceDoc;

    const second = setup(saved, "free");
    const dock = fakeDock();
    await second.controller.start(dock.api);
    expect(second.built).toEqual([]);
    expect(second.controller.mode).toBe("docked");
    expect(second.controller.canvas).toBeNull();
    expect(dock.panels().map((p) => (p.params as { n: number }).n)).toEqual([9]);
    await second.controller.switchTo(0);
    expect(dock.panels()).toEqual([]);
    expect(second.controller.canvas!.windows).toEqual([
      { id: "probe-main", kind: "probe", params: { link: "pink", n: 7 }, x: 40, y: 1200, w: 640, h: 200, z: 1 },
    ]);
  });

  it("opens an old layout (no mode) as docked tiles, as before", async () => {
    const old: WorkspaceDoc = { version: 1, pages: [{ name: "Main", dock: { panels: [{ id: "p1", component: "probe", params: { n: 4 } }] } }], active: 0 };
    const { controller } = setup(old, "free");
    const dock = fakeDock();
    await controller.start(dock.api);
    expect(controller.mode).toBe("docked");
    expect(dock.panels().map((p) => p.id)).toEqual(["p1"]);
  });

  it("switches a page between docked tiles and free windows, keeping its widgets", async () => {
    const { controller, flush, kept } = setup(undefined, "docked");
    const dock = fakeDock();
    await controller.start(dock.api);
    controller.addWidget("probe", { n: 2 });
    controller.setPageMode("free");
    expect(controller.mode).toBe("free");
    expect(dock.panels()).toEqual([]);
    const windows = controller.canvas!.windows;
    expect(windows.map((w) => w.params.n)).toEqual([1, 2]);
    expect(new Set(windows.map((w) => `${w.x},${w.y}`)).size).toBe(2); // not stacked on each other
    await flush();
    expect((kept.get(CURRENT) as WorkspaceDoc).pages[0]!.mode).toBe("free");

    controller.moveWindow(windows[1]!.id, { x: 8, y: 600, w: 300, h: 200 }); // below the first: a second row
    controller.setPageMode("docked");
    expect(controller.canvas).toBeNull();
    expect(dock.panels().map((p) => (p.params as { n: number }).n)).toEqual([1, 2]);
    expect(dock.raw.added.at(-1)).toMatchObject({ position: { direction: "below" } });
  });

  it("keeps tiles where they were when a docked page turns free", async () => {
    const { controller } = setup(undefined, "docked");
    const dock = fakeDock();
    await controller.start(dock.api);
    controller.addWidget("probe", { n: 2 });
    dock.raw.rects.set("probe-main", { left: 104, top: 50, width: 300, height: 500 });
    dock.raw.rects.set(dock.panels()[1]!.id, { left: 408, top: 50, width: 600, height: 500 });
    controller.setPageMode("free");
    expect(controller.canvas!.windows.map(({ x, y, w, h }) => [x, y, w, h])).toEqual([
      [0, 0, 300, 500],
      [304, 0, 600, 500],
    ]);
  });

  it("keeps the showing tab in front when tabs turn into windows", async () => {
    const { controller } = setup(undefined, "docked");
    const dock = fakeDock();
    await controller.start(dock.api);
    controller.addWidget("probe", { n: 2 });
    const second = dock.panels()[1]!.id;
    dock.raw.tabbed.set("probe-main", "g").set(second, "g"); // one group, two tabs
    dock.raw.showing.set("g", "probe-main"); // the first tab is the one showing
    dock.raw.rects.set("g", { left: 10, top: 10, width: 500, height: 400 });
    controller.setPageMode("free");
    const [first, behind] = controller.canvas!.windows;
    expect([first!.x, first!.y, behind!.x, behind!.y]).toEqual([0, 0, 24, 24]); // the tab behind: a step down and right
    expect(first!.z).toBeGreaterThan(behind!.z); // what showed still shows
  });

  it("opens up to 200 windows on a page, and says so after that", async () => {
    const { controller, notes } = setup(undefined, "free");
    await controller.start(fakeDock().api);
    for (let i = 1; i < 200; i += 1) controller.addWidget("probe");
    expect(controller.canvas!.windows).toHaveLength(200);
    expect(notes).toEqual([]);
    controller.addWidget("probe");
    expect(controller.canvas!.windows).toHaveLength(200);
    expect(notes).toEqual(["WARN At most 200 widgets on one page: open another page"]);
  });

  it("pops a window out and back, and says so when the browser blocks it", async () => {
    const { controller, opened, notes, blockPopups } = setup(undefined, "free");
    await controller.start(fakeDock().api);
    expect(await controller.popOut("probe-main")).toBe(true);
    expect(opened[0]).toMatchObject({ url: "/popout.html", box: { width: 400, height: 640 } }); // never too small
    expect(controller.popoutOf("probe-main")).toBeDefined();
    expect(controller.canvas!.windows).toHaveLength(1); // still the page's widget
    controller.dockBack("probe-main");
    expect(opened[0]!.closed).toBe(true);
    expect(controller.popoutOf("probe-main")).toBeUndefined();
    blockPopups();
    expect(await controller.popOut("probe-main")).toBe(false);
    expect(notes.pop()).toContain("allow pop-ups");
  });

  it("closes popped-out windows when their page goes away", async () => {
    const { controller, opened } = setup(undefined, "free");
    await controller.start(fakeDock().api);
    await controller.popOut("probe-main");
    await controller.addPage();
    expect(opened[0]!.closed).toBe(true);
    await controller.switchTo(0);
    expect(controller.canvas!.windows.map((w) => w.id)).toEqual(["probe-main"]);
  });

  it("refuses moving, closing, adding and switching mode while locked", async () => {
    const { controller, notes } = setup(undefined, "free");
    await controller.start(fakeDock().api);
    controller.setLocked(true);
    expect(controller.moveWindow("probe-main", { x: 400, y: 400, w: 400, h: 300 })).toBe(false);
    controller.closeWindow("probe-main");
    controller.addWidget("probe");
    controller.setPageMode("docked");
    expect(controller.mode).toBe("free");
    expect(controller.canvas!.windows).toHaveLength(1);
    expect(notes).toHaveLength(2);
  });

  it("places a new window in an empty spot in view when nothing is beside the front one", async () => {
    const { controller } = setup(undefined, "free");
    await controller.start(fakeDock().api);
    controller.setViewport(() => ({ x: 0, y: 2000, w: 1000, h: 700 })); // scrolled down
    controller.moveWindow("probe-main", { x: 0, y: 0, w: 2000, h: 300 }); // nothing fits right or below in view
    controller.addWidget("probe");
    const added = controller.canvas!.windows[1]!;
    expect(added.y).toBeGreaterThanOrEqual(2000);
    expect(controller.reveal).toBe(added.id); // the canvas scrolls to it
  });
});

describe("uniqueName", () => {
  it("adds a number when the name is taken", () => {
    expect(uniqueName("Page", [])).toBe("Page");
    expect(uniqueName("Page", ["Page", "Page 2"])).toBe("Page 3");
  });
});
