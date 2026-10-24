import type { DockviewApi } from "dockview-react";
import type { MenuItem } from "../components/ContextMenu";
import { fakeRest } from "../test/fakes";
import type { CanvasWindow } from "./canvas";
import { WorkspaceController } from "./controller";
import { LayoutStore, type PageMode } from "./layouts";
import { pageMenu, widgetMenu } from "./menus";
import { allWidgets, registerWidget, type BaseParams } from "./registry";

type P = BaseParams & { lots: number };
registerWidget<P>({
  kind: "m-ladder",
  title: "Ladder",
  description: "test",
  component: () => null,
  defaults: () => ({ link: "pink", lots: 1 }),
  link: "follows",
  tabTitle: (p) => p.instrumentId,
  menu: (p, act) => [{ label: "Lots: 5", checked: p.lots === 5, onSelect: () => act.setParams({ lots: 5 }) }],
});
registerWidget({ kind: "m-chart", title: "Chart", description: "test", component: () => null, defaults: () => ({ link: "none" }), link: "follows" });
registerWidget({ kind: "m-book", title: "Order Book", description: "test", component: () => null, defaults: () => ({ link: "none" }), link: "none" });

const W = (id: string, kind: string, params: Record<string, unknown>, x = 8, y = 8): CanvasWindow => ({
  id,
  kind,
  params: { link: "none", ...params },
  x,
  y,
  w: 400,
  h: 300,
  z: 1,
});

/** A docked page's dockview, as far as the menus use it. */
function fakeDock() {
  const panels = new Map<string, { component: string; params: Record<string, unknown>; closed?: boolean }>();
  const added: Record<string, unknown>[] = [];
  const api = {
    clear: () => panels.clear(),
    toJSON: () => ({}),
    fromJSON: () => {},
    onDidLayoutChange: () => ({ dispose() {} }),
    addPanel: (o: { id: string; component: string; params: Record<string, unknown> }) => {
      added.push(o);
      panels.set(o.id, { component: o.component, params: o.params });
      return { id: o.id };
    },
    get activePanel() {
      return undefined;
    },
    get panels() {
      return [];
    },
    getPanel: (id: string) => {
      const p = panels.get(id);
      if (!p) return undefined;
      return {
        id,
        params: p.params,
        api: {
          component: p.component,
          updateParameters: (next: Record<string, unknown>) => void (p.params = next),
          close: () => void panels.delete(id),
        },
        group: { api: { location: { type: "grid" } } },
      };
    },
  };
  return { api: api as unknown as DockviewApi, panels, added };
}

async function setup(mode: PageMode, windows: CanvasWindow[] = []) {
  const dock = fakeDock();
  const notes: string[] = [];
  const controller = new WorkspaceController({
    layouts: new LayoutStore(fakeRest({}).rest, null, "ayush"),
    newPageMode: mode,
    buildDefault: (api) => {
      api.addPanel({ id: "lad", component: "m-ladder", params: { link: "pink", lots: 1, instrumentId: "NFO:NIFTY" } });
    },
    buildDefaultCanvas: () => windows,
    notify: (level, text) => notes.push(`${level} ${text}`),
    setTimer: () => 0 as unknown as ReturnType<typeof setTimeout>,
    clearTimer: () => {},
    openWindow: () => ({ closed: false, close() {}, addEventListener() {} }) as unknown as Window,
  });
  await controller.start(dock.api);
  return { controller, dock, notes };
}

/** The item with this label, looking inside submenus. */
function find(items: MenuItem[], label: string): Extract<MenuItem, { label: string }> {
  for (const i of items) {
    if (typeof i !== "object" || !("label" in i)) continue;
    if (i.label === label) return i;
    if ("items" in i) {
      try {
        return find(i.items, label);
      } catch {
        // not in this one
      }
    }
  }
  throw new Error(`no item "${label}"`);
}
const choose = (items: MenuItem[], label: string) => {
  const item = find(items, label);
  if (!("onSelect" in item)) throw new Error(`"${label}" opens a submenu`);
  item.onSelect();
};
const labels = (items: MenuItem[]) => items.map((i) => (i === "separator" ? "—" : "header" in i ? `[${i.header}]` : i.label));

const widgets = () => allWidgets().filter((w) => w.kind.startsWith("m-"));
const deps = (controller: WorkspaceController, extra: Partial<Parameters<typeof pageMenu>[0]> = {}) => ({
  controller,
  widgets: widgets(),
  focusSearch: vi.fn(),
  themes: [
    { value: "dark", label: "Dark" },
    { value: "light", label: "Light" },
  ],
  theme: "dark",
  chooseTheme: vi.fn(),
  ...extra,
});

describe("the page's right-click menu", () => {
  it("offers the screen's settings and every widget", async () => {
    const { controller } = await setup("free");
    const items = pageMenu(deps(controller));
    expect(labels(items)).toEqual([
      "[Page: Main]", "Open widget", "Search product…", "—", "This page", "New page", "—",
      "Save layout now", "Lock layout", "Theme",
    ]); // prettier-ignore
    expect(labels((find(items, "Open widget") as { items: MenuItem[] }).items)).toEqual(["Chart", "Ladder", "Order Book"]);
  });

  it("opens a widget where the free page was clicked", async () => {
    const { controller } = await setup("free");
    choose(pageMenu(deps(controller, { at: { x: 613, y: 1201 } })), "Order Book");
    expect(controller.canvas!.windows[0]).toMatchObject({ kind: "m-book", x: 616, y: 1200 }); // on the grid
    expect(controller.reveal).toBe(controller.canvas!.windows[0]!.id);
  });

  it("switches the page between free and docked, renames it and adds pages", async () => {
    const { controller } = await setup("free");
    let items = pageMenu(deps(controller));
    expect(find(items, "Free: any size, anywhere")).toMatchObject({ checked: true });
    expect(find(items, "Close page")).toMatchObject({ disabled: true }); // the only page
    choose(items, "Docked: tiles that fill the page");
    expect(controller.mode).toBe("docked");
    choose(pageMenu(deps(controller)), "Docked");
    await new Promise((r) => setTimeout(r, 0));
    expect(controller.doc.pages.map((p) => p.mode)).toEqual(["docked", "docked"]);
    choose(pageMenu(deps(controller)), "Rename…"); // the page showing: the new one
    expect(controller.renaming).toBe(1);
    items = pageMenu(deps(controller));
    expect(find(items, "Close page")).toMatchObject({ disabled: false });
  });

  it("locks and unlocks; while locked nothing can be added or changed", async () => {
    const { controller } = await setup("free");
    choose(pageMenu(deps(controller)), "Lock layout");
    expect(controller.locked).toBe(true);
    const items = pageMenu(deps(controller));
    expect(find(items, "Open widget")).toMatchObject({ disabled: true });
    expect(find(items, "Docked: tiles that fill the page")).toMatchObject({ disabled: true });
    choose(items, "Unlock layout");
    expect(controller.locked).toBe(false);
  });

  it("searches, saves and picks a theme", async () => {
    const { controller } = await setup("free");
    const d = deps(controller);
    choose(pageMenu(d), "Search product…");
    expect(d.focusSearch).toHaveBeenCalledOnce();
    expect(find(pageMenu(d), "Dark")).toMatchObject({ checked: true });
    choose(pageMenu(d), "Light");
    expect(d.chooseTheme).toHaveBeenCalledWith("light");
  });
});

describe("a widget's right-click menu", () => {
  it("starts with the widget's own settings, then what any widget offers", async () => {
    const { controller } = await setup("free", [W("a", "m-ladder", { link: "pink", lots: 1, instrumentId: "NFO:NIFTY" })]);
    const items = widgetMenu({ controller, id: "a", widgets: widgets() })!;
    expect(labels(items)).toEqual([
      "[Ladder · NFO:NIFTY]", "Lots: 5", "—", "Open for this contract", "Link group",
      "Duplicate", "Pop out into its own window", "—", "Close",
    ]); // prettier-ignore
    choose(items, "Lots: 5");
    expect(controller.canvas!.windows[0]!.params).toMatchObject({ lots: 5, instrumentId: "NFO:NIFTY" });
  });

  it("opens other widgets for the same contract and link group, beside it", async () => {
    const elsewhere = { ...W("z", "m-book", {}, 8, 2000), z: 5 }; // in front, far away
    const { controller } = await setup("free", [W("a", "m-ladder", { link: "pink", lots: 1, instrumentId: "NFO:NIFTY" }), elsewhere]);
    const items = widgetMenu({ controller, id: "a", widgets: widgets() })!;
    expect(labels((find(items, "Open for this contract") as { items: MenuItem[] }).items)).toEqual(["Chart"]); // follows a contract; not itself
    choose(items, "Chart");
    expect(controller.canvas!.windows[2]).toMatchObject({ kind: "m-chart", x: 412, y: 8, params: { instrumentId: "NFO:NIFTY", link: "pink" } });
  });

  it("changes the link group", async () => {
    const { controller } = await setup("free", [W("a", "m-ladder", { link: "pink", lots: 1 })]);
    const items = widgetMenu({ controller, id: "a", widgets: widgets() })!;
    expect(find(items, "Pink")).toMatchObject({ checked: true });
    expect(() => find(items, "Open for this contract")).toThrow(); // no contract yet
    choose(items, "Blue");
    expect(controller.canvas!.windows[0]!.params.link).toBe("blue");
    const book = await setup("free", [W("b", "m-book", {})]);
    expect(() => find(widgetMenu({ controller: book.controller, id: "b", widgets: widgets() })!, "Link group")).toThrow(); // not linked
  });

  it("duplicates, pops out and closes a free window", async () => {
    const { controller } = await setup("free", [W("a", "m-ladder", { link: "pink", lots: 2 })]);
    choose(widgetMenu({ controller, id: "a", widgets: widgets() })!, "Duplicate");
    const [a, copy] = controller.canvas!.windows;
    expect(copy).toMatchObject({ kind: "m-ladder", params: { lots: 2 }, x: a!.x + a!.w + 4, w: a!.w, h: a!.h });
    choose(widgetMenu({ controller, id: "a", widgets: widgets() })!, "Pop out into its own window");
    await new Promise((r) => setTimeout(r, 0));
    const popped = widgetMenu({ controller, id: "a", widgets: widgets() })!;
    choose(popped, "Put back on the page");
    expect(controller.popoutOf("a")).toBeUndefined();
    choose(widgetMenu({ controller, id: "a", widgets: widgets() })!, "Close");
    expect(controller.canvas!.windows.map((w) => w.id)).toEqual([copy!.id]);
  });

  it("works on docked tiles the same way", async () => {
    const { controller, dock } = await setup("docked");
    const items = widgetMenu({ controller, id: "lad", widgets: widgets() })!;
    choose(items, "Lots: 5");
    expect(dock.panels.get("lad")!.params).toMatchObject({ lots: 5, instrumentId: "NFO:NIFTY" });
    choose(widgetMenu({ controller, id: "lad", widgets: widgets() })!, "Duplicate");
    expect(dock.added.at(-1)).toMatchObject({ component: "m-ladder", params: { lots: 5 }, position: { referencePanel: "lad", direction: "right" } });
    controller.setLocked(true);
    controller.closeWidget("lad"); // not while locked
    expect(dock.panels.has("lad")).toBe(true);
    controller.setLocked(false);
    choose(widgetMenu({ controller, id: "lad", widgets: widgets() })!, "Close");
    expect(dock.panels.has("lad")).toBe(false);
    expect(widgetMenu({ controller, id: "lad", widgets: widgets() })).toBeNull(); // gone
  });

  it("can't duplicate, pop out or close while locked", async () => {
    const { controller, notes } = await setup("free", [W("a", "m-ladder", { link: "pink", lots: 1 })]);
    controller.setLocked(true);
    const items = widgetMenu({ controller, id: "a", widgets: widgets() })!;
    for (const label of ["Duplicate", "Pop out into its own window", "Close"]) expect(find(items, label)).toMatchObject({ disabled: true });
    controller.duplicateWidget("a");
    controller.closeWidget("a");
    expect(controller.canvas!.windows).toHaveLength(1);
    expect(notes).toEqual(["INFO The layout is locked: unlock it to add widgets"]);
  });
});
