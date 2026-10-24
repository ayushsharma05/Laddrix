// What the right-click menu offers (#412). Pure builders: they read the workspace and return
// plain items; the menu component draws them and the shell decides which to build.
//
//   right-click on a widget ─► widgetMenu: its own settings, open for this contract,
//                              link group, duplicate, pop out, close
//   right-click elsewhere   ─► pageMenu: open any widget (at the click, on a free page),
//                              search, this page (free/docked, rename, close), new page,
//                              save, lock, theme

import type { MenuItem } from "../components/ContextMenu";
import type { WorkspaceController } from "./controller";
import { LINK_COLOURS, type LinkGroup } from "./links";
import { widget, type BaseParams, type WidgetDef } from "./registry";

const LINKS = Object.keys(LINK_COLOURS) as LinkGroup[];

export interface PageMenuDeps {
  controller: WorkspaceController;
  /** The widgets this user may open (Widgets ▾ shows the same). */
  widgets: WidgetDef[];
  /** On a free page, the canvas point clicked: a widget opened from here goes there. */
  at?: { x: number; y: number };
  focusSearch: () => void;
  themes: { value: string; label: string }[];
  theme: string;
  chooseTheme: (value: string) => void;
}

const byTitle = (a: WidgetDef, b: WidgetDef) => a.title.localeCompare(b.title);

/** Right-click on empty space, the toolbar or the page tabs. */
export function pageMenu(d: PageMenuDeps): MenuItem[] {
  const c = d.controller;
  const locked = c.locked;
  const index = c.doc.active;
  const page = c.doc.pages[index];
  const free = c.mode === "free";
  return [
    { header: page ? `Page: ${page.name}` : "Page" },
    {
      label: "Open widget",
      disabled: locked,
      items: [...d.widgets].sort(byTitle).map((w) => ({ label: w.title, hint: w.description, onSelect: () => c.addWidget(w.kind, {}, free ? d.at : undefined) })),
    },
    { label: "Search product…", hint: "/", onSelect: d.focusSearch },
    "separator",
    {
      label: "This page",
      items: [
        { label: "Free: any size, anywhere", checked: free, disabled: locked, onSelect: () => c.setPageMode("free") },
        { label: "Docked: tiles that fill the page", checked: !free, disabled: locked, onSelect: () => c.setPageMode("docked") },
        "separator",
        { label: "Rename…", onSelect: () => c.requestRename(index) },
        { label: "Close page", disabled: c.doc.pages.length <= 1, onSelect: () => void c.removePage(index) },
      ],
    },
    {
      label: "New page",
      items: [
        { label: "Free", onSelect: () => void c.addPage(undefined, "free") },
        { label: "Docked", onSelect: () => void c.addPage(undefined, "docked") },
      ],
    },
    "separator",
    { label: "Save layout now", hint: "Ctrl+S", onSelect: () => void c.saveNow() },
    { label: locked ? "Unlock layout" : "Lock layout", onSelect: () => c.setLocked(!locked) },
    { label: "Theme", items: d.themes.map((t) => ({ label: t.label, checked: t.value === d.theme, onSelect: () => d.chooseTheme(t.value) })) },
  ];
}

export interface WidgetMenuDeps {
  controller: WorkspaceController;
  /** The widget right-clicked (its panel or window id). */
  id: string;
  /** The widgets this user may open (for "Open for this contract"). */
  widgets: WidgetDef[];
}

/** Right-click on a widget; null if it is gone (closed a moment ago). */
export function widgetMenu({ controller: c, id, widgets }: WidgetMenuDeps): MenuItem[] | null {
  const now = c.widgetAt(id);
  if (!now) return null;
  const def = widget(now.kind);
  const params = now.params;
  const locked = c.locked;
  const title = def ? (def.tabTitle?.(params) ?? def.title) : now.kind;
  const setParams = (change: Partial<BaseParams> & Record<string, unknown>) => c.updateWidget(id, change);
  const open = (kind: string, extra: Partial<BaseParams> & Record<string, unknown>) => {
    if (c.canvas) c.focusWindow(id); // a new window goes beside this one
    c.addWidget(kind, extra);
  };
  const items: MenuItem[] = [{ header: def && title !== def.title ? `${def.title} · ${title}` : title }];

  const own = def?.menu?.(params, { setParams, open }) ?? [];
  if (own.length) items.push(...own, "separator");

  const instrumentId = params.instrumentId;
  if (instrumentId) {
    const others = [...widgets].filter((w) => w.link === "follows" && w.kind !== now.kind).sort(byTitle);
    if (others.length)
      items.push({
        label: "Open for this contract",
        disabled: locked,
        items: others.map((w) => ({ label: w.title, onSelect: () => open(w.kind, { instrumentId, link: params.link ?? "none" }) })),
      });
  }
  if (def && def.link !== "none") {
    const link = params.link ?? "none";
    items.push({
      label: "Link group",
      items: LINKS.map((l) => ({ label: l === "none" ? "Not linked" : l[0]!.toUpperCase() + l.slice(1), checked: l === link, onSelect: () => setParams({ link: l }) })),
    });
  }
  items.push(
    { label: "Duplicate", disabled: locked, onSelect: () => c.duplicateWidget(id) },
    now.popped
      ? { label: "Put back on the page", onSelect: () => c.dockBack(id) }
      : { label: "Pop out into its own window", disabled: locked, onSelect: () => void c.popOut(id) },
    "separator",
    { label: "Close", disabled: locked, onSelect: () => c.closeWidget(id) },
  );
  return items;
}
