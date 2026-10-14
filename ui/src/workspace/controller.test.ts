import type { DockviewApi } from "dockview-react";
import { fakeRest } from "../test/fakes";
import { WorkspaceController, uniqueName } from "./controller";
import { CURRENT, LayoutStore, type WorkspaceDoc } from "./layouts";
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
    addPanel: (o: { id: string; component: string; params: unknown }) => {
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

function setup(saved?: WorkspaceDoc) {
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
  const controller = new WorkspaceController({
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
  return { controller, kept, timers, notes, built, flush };
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

describe("uniqueName", () => {
  it("adds a number when the name is taken", () => {
    expect(uniqueName("Page", [])).toBe("Page");
    expect(uniqueName("Page", ["Page", "Page 2"])).toBe("Page 3");
  });
});
