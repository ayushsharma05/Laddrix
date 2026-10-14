// The workspace behind the screen: its pages (Main, Options, …), which one is showing, and
// saving. React components call it; it calls dockview. No React in here, so it is easy to test.
//
//   any move/resize/param change ─► changed() ─► wait 1.5 s of quiet ─► save "current"
//   switchTo(page) ─► remember this page's layout ─► show the other page
//   saveAs(name) / open(name) ─► named layouts on the server (Layouts ▾)

import type { DockviewApi } from "dockview-react";
import { ApiError } from "../api/rest";
import { CURRENT, MAX_PAGES, type LayoutStore, type WorkspaceDoc } from "./layouts";
import { widget, type BaseParams } from "./registry";

type Timer = ReturnType<typeof setTimeout>;
export type Notify = (level: "ERROR" | "WARN" | "INFO" | "OK", text: string) => void;

export interface ControllerOptions {
  layouts: LayoutStore;
  /** Fills an empty page (the first-run Main page gets the starter widgets). */
  buildDefault: (api: DockviewApi, pageName: string) => Promise<void> | void;
  notify: Notify;
  autosaveMs?: number;
  setTimer?: (fn: () => void, ms: number) => Timer;
  clearTimer?: (t: Timer) => void;
}

export function freshDoc(): WorkspaceDoc {
  return { version: 1, pages: [{ name: "Main", dock: null }], active: 0 };
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

let panelCounter = 0;
export function newPanelId(kind: string): string {
  panelCounter += 1;
  return `${kind}-${Date.now().toString(36)}-${panelCounter}`;
}

export class WorkspaceController {
  doc: WorkspaceDoc = freshDoc();
  locked = false;
  saveState: "saved" | "saving" | "unsaved" | "browser-only" = "saved";
  private api: DockviewApi | null = null;
  private restoring = false;
  private timer: Timer | null = null;
  private listeners = new Set<() => void>();
  private version = 0;
  private warnedSave = false;

  constructor(private readonly opts: ControllerOptions) {}

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
    this.doc = saved ?? freshDoc();
    await this.show(this.doc.active);
    api.onDidLayoutChange(() => this.changed());
  }

  /** The whole workspace as it is right now (the showing page's layout read from dockview). */
  snapshot(): WorkspaceDoc {
    const pages = this.doc.pages.map((p, i) =>
      i === this.doc.active && this.api ? { name: p.name, dock: this.api.toJSON() } : p,
    );
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
      if (page.dock) {
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

  // --- pages --------------------------------------------------------------------------------

  async switchTo(index: number): Promise<void> {
    if (index === this.doc.active || !this.doc.pages[index]) return;
    this.capture();
    await this.show(index);
    this.changed();
  }

  async addPage(name?: string): Promise<void> {
    if (this.doc.pages.length >= MAX_PAGES) {
      this.opts.notify("WARN", `At most ${MAX_PAGES} pages`);
      return;
    }
    this.capture();
    const title = uniqueName(name?.trim() || "Page", this.doc.pages.map((p) => p.name));
    this.doc = { ...this.doc, pages: [...this.doc.pages, { name: title, dock: null }] };
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
    const location = this.api?.getPanel(panelId)?.group.api.location;
    if (location?.type === "popout") location.getWindow().close();
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
    this.doc = freshDoc();
    await this.show(0);
    this.changed();
  }

  dispose(): void {
    if (this.timer) (this.opts.clearTimer ?? clearTimeout)(this.timer);
    this.timer = null;
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
