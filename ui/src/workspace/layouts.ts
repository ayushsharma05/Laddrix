// Saving and restoring the workspace: which widgets are open, where, and with what settings.
//
//   save(name, doc) ─► browser storage (instant, survives a server outage)
//                   └► PUT /api/layouts/{name} (follows the user to any computer)
//   load(name)      ─► GET /api/layouts/{name}; if the server can't be reached, browser storage
//
// The server is the source of truth; browser storage is the fallback, kept per user so two
// people sharing a browser never see each other's layouts.

import { ApiError, type Rest } from "../api/rest";
import { isCanvas, type Canvas } from "./canvas";

/**
 * How a page arranges its widgets (#410):
 * - "free": windows anywhere, any size, gaps allowed, the page scrolls (canvas.ts)
 * - "docked": tiles that always fill the page (dockview)
 * Pages saved before #410 have no mode: they are docked.
 */
export type PageMode = "free" | "docked";

/** One workspace tab (Main, Options, …) and its widgets. */
export interface Page {
  name: string;
  mode?: PageMode; // missing: "docked"
  dock: unknown; // a docked page: dockview's own JSON (api.toJSON()); null for a fresh page
  canvas?: Canvas | null; // a free page: its windows; null or missing for a fresh page
}

export function modeOf(page: Page): PageMode {
  return page.mode === "free" ? "free" : "docked";
}

function isPage(value: unknown): value is Page {
  if (!value || typeof value !== "object") return false;
  const p = value as Partial<Page>;
  if (typeof p.name !== "string" || !("dock" in p)) return false;
  if (p.mode !== undefined && p.mode !== "free" && p.mode !== "docked") return false;
  return p.canvas === undefined || p.canvas === null || isCanvas(p.canvas);
}

export interface WorkspaceDoc {
  version: 1;
  pages: Page[];
  active: number; // index into pages
}

export const CURRENT = "current"; // the autosaved workspace
export const MAX_PAGES = 12;

export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export function isWorkspaceDoc(value: unknown): value is WorkspaceDoc {
  if (!value || typeof value !== "object") return false;
  const doc = value as Partial<WorkspaceDoc>;
  return (
    doc.version === 1 &&
    Array.isArray(doc.pages) &&
    doc.pages.length > 0 &&
    doc.pages.length <= MAX_PAGES &&
    doc.pages.every(isPage) &&
    typeof doc.active === "number" &&
    doc.active >= 0 &&
    doc.active < doc.pages.length
  );
}

export type SavedTo = "server" | "browser";

export class LayoutStore {
  constructor(
    private readonly rest: Rest,
    private readonly storage: StorageLike | null,
    private readonly userId: string,
  ) {}

  private key(name: string): string {
    return `mocktrade.layout.${this.userId}.${name}`;
  }

  /** The saved workspace, or null if there is none (or it is damaged). */
  async load(name: string): Promise<WorkspaceDoc | null> {
    try {
      const saved = await this.rest.get<{ layout: unknown }>(`/api/layouts/${encodeURIComponent(name)}`);
      if (isWorkspaceDoc(saved.layout)) return saved.layout;
      return this.loadLocal(name);
    } catch (err) {
      if (err instanceof ApiError && err.loggedOut) throw err;
      // 404 (never saved on the server) or the server is unreachable: use the browser's copy
      return this.loadLocal(name);
    }
  }

  /** Saves to the browser at once, then to the server. Says where it ended up. */
  async save(name: string, doc: WorkspaceDoc): Promise<SavedTo> {
    try {
      this.storage?.setItem(this.key(name), JSON.stringify(doc));
    } catch {
      // storage full or blocked (private window): the server copy still counts
    }
    try {
      await this.rest.put(`/api/layouts/${encodeURIComponent(name)}`, { layout: doc });
      return "server";
    } catch (err) {
      if (err instanceof ApiError && (err.loggedOut || err.status === 422)) throw err;
      return "browser";
    }
  }

  /** Names saved on the server (the autosaved "current" is left out). */
  async list(): Promise<string[]> {
    const rows = await this.rest.get<{ name: string }[]>("/api/layouts");
    return rows.map((r) => r.name).filter((n) => n !== CURRENT);
  }

  async remove(name: string): Promise<void> {
    try {
      this.storage?.removeItem(this.key(name));
    } catch {
      // nothing to do
    }
    await this.rest.delete(`/api/layouts/${encodeURIComponent(name)}`);
  }

  private loadLocal(name: string): WorkspaceDoc | null {
    try {
      const text = this.storage?.getItem(this.key(name));
      if (!text) return null;
      const doc: unknown = JSON.parse(text);
      return isWorkspaceDoc(doc) ? doc : null;
    } catch {
      return null;
    }
  }
}

/** Browser storage if the browser allows it (it can throw in private windows). */
export function browserStorage(): StorageLike | null {
  try {
    const storage = window.localStorage;
    const probe = "mocktrade.probe";
    storage.setItem(probe, "1");
    storage.removeItem(probe);
    return storage;
  } catch {
    return null;
  }
}
