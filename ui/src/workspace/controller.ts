// The workspace behind the screen: its pages (Main, Options, …), which one is showing, and
// saving. React components call it; it calls dockview. No React in here, so it is easy to test.
//
//   any move/resize/param change ─► changed() ─► wait 1.5 s of quiet ─► save "current"
//   switchTo(page) ─► remember this page's layout ─► show the other page
//   saveAs(name) / open(name) ─► named layouts on the server (Layouts ▾)
//
// Each page is free or docked (#410):
//   free    the windows live here (this.canvas); the canvas component draws them
//   docked  the tiles live in dockview (this.api)
// Dockview stays mounted (hidden, and empty) while a free page shows, so switching is quick.

import type { DockviewApi } from "dockview-react";
import { ApiError } from "../api/rest";
import { DEFAULT_SIZE, MAX_WINDOWS, placeNew, tidy, toFront, topZ, type Box, type Canvas, type CanvasWindow } from "./canvas";
import { CURRENT, MAX_PAGES, modeOf, type LayoutStore, type PageMode, type WorkspaceDoc } from "./layouts";
import { widget, type BaseParams } from "./registry";

type Timer = ReturnType<typeof setTimeout>;
export type Notify = (level: "ERROR" | "WARN" | "INFO" | "OK", text: string) => void;

export interface ControllerOptions {
  layouts: LayoutStore;
  /** Fills an empty page (the first-run Main page gets the starter widgets). */
  buildDefault: (api: DockviewApi, pageName: string) => Promise<void> | void;
  /** The same for a free page: the windows it starts with. */
  buildDefaultCanvas?: (pageName: string) => Promise<CanvasWindow[]> | CanvasWindow[];
  /** How new pages (and the first-run Main page) arrange widgets. Default "free". */
  newPageMode?: PageMode;
  /** Opens a pop-out window for a free page's widget (default: window.open). */
  openWindow?: (url: string, box: { left: number; top: number; width: number; height: number }) => Window | null;
  notify: Notify;
  autosaveMs?: number;
  setTimer?: (fn: () => void, ms: number) => Timer;
  clearTimer?: (t: Timer) => void;
}

export function freshDoc(mode: PageMode = "free"): WorkspaceDoc {
  return { version: 1, pages: [{ name: "Main", mode, dock: null, canvas: null }], active: 0 };
}

/** The page a popped-out widget's window loads (ui/public/popout.html). */
export const POPOUT_URL = "/popout.html";

/** Where a popped-out window opens: over the widget, never smaller than the widget wants. */
export function popoutBox(panel: { api: { component: string }; group?: { element?: Element } }): {
  left: number;
  top: number;
  width: number;
  height: number;
} {
  const rect = panel.group?.element?.getBoundingClientRect?.();
  const want = widget(panel.api.component)?.size;
  return {
    left: Math.round(rect?.left ?? 80),
    top: Math.round(rect?.top ?? 80),
    width: Math.max(Math.round(rect?.width ?? 0), want?.width ?? 0, 360),
    height: Math.max(Math.round(rect?.height ?? 0), want?.height ?? 0, 640),
  };
}

/** A free page's widget in its own window, over where it was, never smaller than the widget wants. */
export function popoutBoxFor(win: CanvasWindow, view: Box): { left: number; top: number; width: number; height: number } {
  const want = widget(win.kind)?.size;
  return {
    left: Math.max(0, Math.round(win.x - view.x)),
    top: Math.max(0, Math.round(win.y - view.y)),
    width: Math.max(win.w, want?.width ?? 0, 360),
    height: Math.max(win.h, want?.height ?? 0, 640),
  };
}

function openPopout(url: string, box: { left: number; top: number; width: number; height: number }): Window | null {
  const features = `popup,left=${box.left},top=${box.top},width=${box.width},height=${box.height}`;
  return window.open(url, "_blank", features);
}

let panelCounter = 0;
export function newPanelId(kind: string): string {
  panelCounter += 1;
  return `${kind}-${Date.now().toString(36)}-${panelCounter}`;
}

export class WorkspaceController {
  doc: WorkspaceDoc = freshDoc();
  /** The showing page's windows when it is free; null when it is docked. */
  canvas: Canvas | null = null;
  /** A window just added: the canvas scrolls to it once. */
  reveal: string | null = null;
  locked = false;
  saveState: "saved" | "saving" | "unsaved" | "browser-only" = "saved";
  private api: DockviewApi | null = null;
  private restoring = false;
  private timer: Timer | null = null;
  private listeners = new Set<() => void>();
  private version = 0;
  private warnedSave = false;
  private viewport: () => Box = () => ({ x: 0, y: 0, w: 1200, h: 800 });
  private popouts = new Map<string, Window>();

  constructor(private readonly opts: ControllerOptions) {}

  private get newMode(): PageMode {
    return this.opts.newPageMode ?? "free";
  }

  /** How the showing page arranges its widgets. */
  get mode(): PageMode {
    const page = this.doc.pages[this.doc.active];
    return page ? modeOf(page) : this.newMode;
  }

  /** The canvas component says which part of the canvas is on screen (for placing new windows). */
  setViewport(read: () => Box): () => void {
    this.viewport = read;
    return () => {
      if (this.viewport === read) this.viewport = () => ({ x: 0, y: 0, w: 1200, h: 800 });
    };
  }

  /** Where layouts are kept (for the Layouts menu). */
  get layouts(): LayoutStore {
    return this.opts.layouts;
  }

  // --- for React (useSyncExternalStore) --------------------------------------------------------

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getVersion = (): number => this.version;

  private emit(): void {
    this.version += 1;
    for (const listener of this.listeners) listener();
  }

  // --- start --------------------------------------------------------------------------------

  /** Called once dockview is ready: restore the autosaved workspace or build the default. */
  async start(api: DockviewApi): Promise<void> {
    this.api = api;
    let saved: WorkspaceDoc | null = null;
    try {
      saved = await this.opts.layouts.load(CURRENT);
    } catch (err) {
      if (err instanceof ApiError && err.loggedOut) return;
    }
    this.doc = saved ?? freshDoc(this.newMode);
    await this.show(this.doc.active);
    api.onDidLayoutChange(() => this.changed());
  }

  /** The whole workspace as it is right now (the showing page read from dockview or the canvas). */
  snapshot(): WorkspaceDoc {
    const pages = this.doc.pages.map((p, i) => {
      if (i !== this.doc.active || !this.api) return p;
      if (modeOf(p) === "free") return { name: p.name, mode: "free" as const, dock: null, canvas: this.canvas };
      return { name: p.name, mode: "docked" as const, dock: this.api.toJSON() };
    });
    return { version: 1, pages, active: this.doc.active };
  }

  private capture(): void {
    this.doc = this.snapshot();
  }

  private async show(index: number): Promise<void> {
    const api = this.api;
    if (!api) return;
    const page = this.doc.pages[index] ?? this.doc.pages[0]!;
    this.restoring = true;
    try {
      api.clear();
      this.closePopouts();
      this.canvas = null;
      this.reveal = null;
      if (modeOf(page) === "free") {
        this.canvas = { windows: page.canvas ? page.canvas.windows : await this.defaultWindows(page.name) };
      } else if (page.dock) {
        try {
          api.fromJSON(page.dock as Parameters<DockviewApi["fromJSON"]>[0]);
        } catch {
          api.clear();
          this.opts.notify("WARN", `The saved "${page.name}" page couldn't be opened; it was reset`);
          await this.opts.buildDefault(api, page.name);
        }
      } else {
        await this.opts.buildDefault(api, page.name);
      }
      this.doc = { ...this.doc, active: Math.max(0, this.doc.pages.indexOf(page)) };
    } finally {
      this.restoring = false;
    }
    this.emit();
  }

  private async defaultWindows(pageName: string): Promise<CanvasWindow[]> {
    try {
      return (await this.opts.buildDefaultCanvas?.(pageName)) ?? [];
    } catch {
      return []; // the starter widgets are a nicety: an empty page still works
    }
  }

  // --- pages --------------------------------------------------------------------------------

  async switchTo(index: number): Promise<void> {
    if (index === this.doc.active || !this.doc.pages[index]) return;
    this.capture();
    await this.show(index);
    this.changed();
  }

  async addPage(name?: string, mode: PageMode = this.newMode): Promise<void> {
    if (this.doc.pages.length >= MAX_PAGES) {
      this.opts.notify("WARN", `At most ${MAX_PAGES} pages`);
      return;
    }
    this.capture();
    const title = uniqueName(name?.trim() || "Page", this.doc.pages.map((p) => p.name));
    this.doc = { ...this.doc, pages: [...this.doc.pages, { name: title, mode, dock: null, canvas: null }] };
    await this.show(this.doc.pages.length - 1);
    this.changed();
  }

  renamePage(index: number, name: string): void {
    const clean = name.trim().slice(0, 24);
    const page = this.doc.pages[index];
    if (!page || !clean || clean === page.name) return;
    const others = this.doc.pages.filter((_, i) => i !== index).map((p) => p.name);
    const pages = this.doc.pages.map((p, i) => (i === index ? { ...p, name: uniqueName(clean, others) } : p));
    this.doc = { ...this.doc, pages };
    this.emit();
    this.changed();
  }

  async removePage(index: number): Promise<void> {
    if (this.doc.pages.length <= 1 || !this.doc.pages[index]) return;
    this.capture();
    const pages = this.doc.pages.filter((_, i) => i !== index);
    const active = this.doc.active;
    const next = index < active ? active - 1 : index === active ? Math.max(0, index - 1) : active;
    this.doc = { ...this.doc, pages, active: Math.min(next, pages.length - 1) };
    await this.show(this.doc.active);
    this.changed();
  }

  /**
   * Switch the showing page between free windows and docked tiles, keeping its widgets:
   * docked → free keeps each tile where it was; free → docked tiles them row by row.
   */
  setPageMode(mode: PageMode): void {
    const api = this.api;
    const index = this.doc.active;
    const page = this.doc.pages[index];
    if (!api || !page || modeOf(page) === mode) return;
    if (this.locked) {
      this.opts.notify("INFO", "The layout is locked: unlock it to change the page");
      return;
    }
    this.restoring = true;
    try {
      if (mode === "free") {
        const windows = this.windowsFromDock(api);
        api.clear();
        this.canvas = { windows };
      } else {
        const windows = this.canvas?.windows ?? [];
        this.closePopouts();
        this.canvas = null;
        this.dockWindows(api, windows);
      }
    } finally {
      this.restoring = false;
    }
    const pages = this.doc.pages.map((p, i) => (i === index ? { name: p.name, mode, dock: null, canvas: null } : p));
    this.doc = { ...this.doc, pages };
    this.emit();
    this.changed();
  }

  /** Dockview's tiles as windows at the same places (a tab behind another sits a step down and right). */
  private windowsFromDock(api: DockviewApi): CanvasWindow[] {
    const panels = api.panels;
    const rects = panels.map((p) =>
      p.group?.api.location.type === "grid" ? p.group.element?.getBoundingClientRect?.() : undefined,
    );
    const seen = rects.filter((r): r is DOMRect => !!r && r.width > 0 && r.height > 0);
    const left = Math.min(...seen.map((r) => r.left));
    const top = Math.min(...seen.map((r) => r.top));
    const tabs = new Map<unknown, number>();
    const out: CanvasWindow[] = [];
    for (const [i, panel] of panels.entries()) {
      const r = rects[i];
      const kind = panel.api.component;
      const n = tabs.get(panel.group) ?? 0;
      tabs.set(panel.group, n + 1);
      const size = { w: widget(kind)?.size?.width ?? DEFAULT_SIZE.w, h: widget(kind)?.size?.height ?? DEFAULT_SIZE.h };
      const box =
        r && r.width > 0 && r.height > 0
          ? tidy({ x: r.left - left + n * 24, y: r.top - top + n * 24, w: r.width, h: r.height })
          : placeNew(out, size, this.viewport());
      const front = panel.group?.activePanel?.id === panel.id ? panels.length : 0;
      out.push({ id: panel.id, kind, params: { ...(panel.params as BaseParams) }, ...box, z: i + 1 + front });
    }
    return out;
  }

  /** Windows as dockview tiles: top to bottom in rows, each row left to right. */
  private dockWindows(api: DockviewApi, windows: CanvasWindow[]): void {
    const known = windows.filter((w) => widget(w.kind));
    if (known.length < windows.length) {
      this.opts.notify("WARN", `${windows.length - known.length} widget(s) this version doesn't have were left out`);
    }
    const sorted = [...known].sort((a, b) => a.y - b.y || a.x - b.x);
    let prev: string | null = null;
    let rowTop = 0;
    let rowBottom = 0;
    for (const w of sorted) {
      const newRow = prev === null || w.y >= (rowTop + rowBottom) / 2;
      const position = prev === null ? undefined : newRow ? { direction: "below" as const } : { referencePanel: prev, direction: "right" as const };
      if (newRow) [rowTop, rowBottom] = [w.y, w.y + w.h];
      else rowBottom = Math.max(rowBottom, w.y + w.h);
      const def = widget(w.kind)!;
      api.addPanel({
        id: w.id,
        component: w.kind,
        params: w.params,
        title: def.tabTitle?.(w.params) ?? def.title,
        ...(position ? { position } : {}),
      });
      prev = w.id;
    }
  }

  // --- widgets ------------------------------------------------------------------------------

  /** Open a widget next to the active one (ladders to the right, grids below). */
  addWidget(kind: string, params: Partial<BaseParams> & Record<string, unknown> = {}): void {
    const api = this.api;
    const def = widget(kind);
    if (!api || !def) return;
    if (this.locked) {
      this.opts.notify("INFO", "The layout is locked: unlock it to add widgets");
      return;
    }
    const merged = { ...def.defaults(), ...params };
    if (this.canvas) {
      const windows = this.canvas.windows;
      if (windows.length >= MAX_WINDOWS) {
        this.opts.notify("WARN", `At most ${MAX_WINDOWS} widgets on one page: open another page`);
        return;
      }
      const from = windows.reduce<CanvasWindow | undefined>((top, w) => (!top || w.z > top.z ? w : top), undefined);
      const size = { w: def.size?.width ?? DEFAULT_SIZE.w, h: def.size?.height ?? DEFAULT_SIZE.h };
      const box = placeNew(windows, size, this.viewport(), from, def.link === "follows" ? "right" : "below");
      const added: CanvasWindow = { id: newPanelId(kind), kind, params: merged, ...box, z: topZ(windows) };
      this.reveal = added.id;
      this.setWindows([...windows, added]);
      return;
    }
    const active = api.activePanel;
    api.addPanel({
      id: newPanelId(kind),
      component: kind,
      params: merged,
      title: def.tabTitle?.(merged) ?? def.title,
      ...(active
        ? { position: { referencePanel: active.id, direction: def.link === "follows" ? "right" : "below" } }
        : {}),
      ...(def.size?.width ? { initialWidth: def.size.width } : {}),
      ...(def.size?.height ? { initialHeight: def.size.height } : {}),
    });
  }

  /**
   * Move a widget into its own window (e.g. onto another monitor). The window belongs to this
   * page: it shares the same live connection, store and link groups, so the widget keeps its
   * settings and keeps updating. Closing the window docks the widget back.
   */
  async popOut(panelId: string): Promise<boolean> {
    if (this.canvas) return this.popOutWindow(panelId);
    const api = this.api;
    const panel = api?.getPanel(panelId);
    if (!api || !panel) return false;
    if (this.locked) {
      this.opts.notify("INFO", "The layout is locked: unlock it to move widgets");
      return false;
    }
    const opened = await api.addPopoutGroup(panel, { popoutUrl: POPOUT_URL, position: popoutBox(panel) });
    if (!opened) {
      this.opts.notify("WARN", "The window was blocked: allow pop-ups for MockTrade to pop widgets out");
      return false;
    }
    this.changed();
    return true;
  }

  /** Put a popped-out widget back into the workspace (closing its window does the same). */
  dockBack(panelId: string): void {
    const popped = this.popouts.get(panelId);
    if (popped) {
      this.popouts.delete(panelId);
      try {
        if (!popped.closed) popped.close();
      } catch {
        // already gone
      }
      this.emit();
      return;
    }
    const location = this.api?.getPanel(panelId)?.group.api.location;
    if (location?.type === "popout") location.getWindow().close();
  }

  // --- a free page's windows -----------------------------------------------------------------

  private setWindows(windows: CanvasWindow[]): void {
    this.canvas = { windows };
    this.emit();
    this.changed();
  }

  /** A window dragged or resized to `box` (already snapped). False while locked. */
  moveWindow(id: string, box: Box): boolean {
    if (!this.canvas || this.locked) return false;
    const next = tidy(box);
    let moved = false;
    const windows = this.canvas.windows.map((w) => {
      if (w.id !== id || (w.x === next.x && w.y === next.y && w.w === next.w && w.h === next.h)) return w;
      moved = true;
      return { ...w, ...next };
    });
    if (moved) this.setWindows(windows);
    return moved;
  }

  /** Bring a window in front of the others (a click anywhere on it). */
  focusWindow(id: string): void {
    if (!this.canvas) return;
    const windows = toFront(this.canvas.windows, id);
    if (windows !== this.canvas.windows) this.setWindows(windows);
  }

  closeWindow(id: string): void {
    if (!this.canvas || this.locked) return;
    if (this.popouts.has(id)) this.dockBack(id);
    this.setWindows(this.canvas.windows.filter((w) => w.id !== id));
  }

  /** A widget changed its saved settings (or its link group). */
  setWindowParams(id: string, params: BaseParams & Record<string, unknown>): void {
    if (!this.canvas) return;
    this.setWindows(this.canvas.windows.map((w) => (w.id === id ? { ...w, params } : w)));
  }

  /** The window a free page's widget is popped out into, if it is. */
  popoutOf(id: string): Window | undefined {
    return this.popouts.get(id);
  }

  private popOutWindow(id: string): boolean {
    const win = this.canvas?.windows.find((w) => w.id === id);
    if (!win) return false;
    if (this.locked) {
      this.opts.notify("INFO", "The layout is locked: unlock it to move widgets");
      return false;
    }
    if (this.popouts.has(id)) return true;
    const opened = (this.opts.openWindow ?? openPopout)(POPOUT_URL, popoutBoxFor(win, this.viewport()));
    if (!opened) {
      this.opts.notify("WARN", "The window was blocked: allow pop-ups for MockTrade to pop widgets out");
      return false;
    }
    this.popouts.set(id, opened);
    this.emit();
    return true;
  }

  /** Popped-out windows close when their page goes (their widgets stay on the page). */
  private closePopouts(): void {
    const open = [...this.popouts.values()];
    this.popouts.clear();
    for (const w of open) {
      try {
        if (!w.closed) w.close();
      } catch {
        // already gone
      }
    }
  }

  setLocked(locked: boolean): void {
    this.locked = locked;
    this.emit();
  }

  // --- saving -------------------------------------------------------------------------------

  /** Something moved or a setting changed: save after a short quiet spell. */
  changed(): void {
    if (this.restoring || !this.api) return;
    if (this.saveState !== "unsaved") {
      this.saveState = "unsaved";
      this.emit();
    }
    const clear = this.opts.clearTimer ?? clearTimeout;
    if (this.timer) clear(this.timer);
    this.timer = (this.opts.setTimer ?? setTimeout)(() => {
      this.timer = null;
      void this.saveNow();
    }, this.opts.autosaveMs ?? 1500);
  }

  async saveNow(): Promise<void> {
    if (!this.api) return;
    this.saveState = "saving";
    this.emit();
    try {
      const where = await this.opts.layouts.save(CURRENT, this.snapshot());
      this.saveState = where === "server" ? "saved" : "browser-only";
      if (where === "browser" && !this.warnedSave) {
        this.warnedSave = true;
        this.opts.notify("WARN", "The layout was saved in this browser only: the server didn't answer");
      }
      if (where === "server") this.warnedSave = false;
    } catch (err) {
      this.saveState = "unsaved";
      this.opts.notify("ERROR", `The layout wasn't saved: ${err instanceof Error ? err.message : String(err)}`);
    }
    this.emit();
  }

  async saveAs(name: string): Promise<boolean> {
    const clean = name.trim();
    if (!clean || clean === CURRENT) {
      this.opts.notify("WARN", "Give the layout a name");
      return false;
    }
    try {
      const where = await this.opts.layouts.save(clean, this.snapshot());
      this.opts.notify(
        where === "server" ? "OK" : "WARN",
        where === "server" ? `Layout "${clean}" saved` : `Layout "${clean}" saved in this browser only`,
      );
      return true;
    } catch (err) {
      this.opts.notify("ERROR", `Layout "${clean}" wasn't saved: ${err instanceof Error ? err.message : String(err)}`);
      return false;
    }
  }

  async open(name: string): Promise<void> {
    const doc = await this.opts.layouts.load(name);
    if (!doc) {
      this.opts.notify("WARN", `There is no layout called "${name}"`);
      return;
    }
    this.doc = doc;
    await this.show(doc.active);
    this.changed();
    this.opts.notify("OK", `Layout "${name}" opened`);
  }

  /** Back to the starter workspace (one Main page). */
  async reset(): Promise<void> {
    this.doc = freshDoc(this.newMode);
    await this.show(0);
    this.changed();
  }

  dispose(): void {
    if (this.timer) (this.opts.clearTimer ?? clearTimeout)(this.timer);
    this.timer = null;
    this.closePopouts();
    this.listeners.clear();
  }
}

/** "Page", ["Page"] -> "Page 2". */
export function uniqueName(name: string, taken: string[]): string {
  if (!taken.includes(name)) return name;
  for (let n = 2; ; n += 1) {
    const candidate = `${name} ${n}`;
    if (!taken.includes(candidate)) return candidate;
  }
}
