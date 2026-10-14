// The desktop: dockview with every widget kind registered, plus the page tabs along the
// bottom (Main | Options | … | +), like a trading terminal's workspace tabs.

import { DockviewReact, type DockviewReadyEvent, type DockviewTheme } from "dockview-react";
import { useMemo, useState, useSyncExternalStore, type ReactNode } from "react";
import { useStore } from "../app/context";
import type { WorkspaceController } from "./controller";
import { ControllerContext, WidgetTab, panelFor } from "./Panel";
import { allWidgets } from "./registry";

const theme: DockviewTheme = { name: "mocktrade", className: "dockview-theme-mocktrade", gap: 4 };

export function Workspace({ controller, footer }: { controller: WorkspaceController; footer?: ReactNode }) {
  useSyncExternalStore(controller.subscribe, controller.getVersion);
  const components = useMemo(
    () => Object.fromEntries(allWidgets().map((w) => [w.kind, panelFor(w.kind)])),
    [],
  );
  const onReady = (event: DockviewReadyEvent) => {
    void controller.start(event.api);
  };
  return (
    <ControllerContext.Provider value={controller}>
      <div className="desktop">
        <DockviewReact
          components={components}
          defaultTabComponent={WidgetTab}
          onReady={onReady}
          theme={theme}
          locked={controller.locked}
          disableDnd={controller.locked}
          watermarkComponent={EmptyPage}
        />
      </div>
      <PageTabs controller={controller}>{footer}</PageTabs>
    </ControllerContext.Provider>
  );
}

function EmptyPage() {
  return (
    <div className="watermark">
      <p>This page is empty.</p>
      <p>Open widgets from <b>Widgets ▾</b> or search a product with <kbd>/</kbd>.</p>
    </div>
  );
}

function PageTabs({ controller, children }: { controller: WorkspaceController; children?: ReactNode }) {
  const [editing, setEditing] = useState<number | null>(null);
  const working = useStore((s) => (s.accountId ? s.books[s.accountId]?.orders : undefined));
  const workingCount = useMemo(
    () => Object.values(working ?? {}).filter((o) => o.status === "OPEN" || o.status === "PARTIAL" || o.status === "PENDING").length,
    [working],
  );
  const { pages, active } = controller.doc;
  return (
    <div className="page-tabs" role="tablist" aria-label="Workspace pages">
      {pages.map((page, i) =>
        editing === i ? (
          <input
            key={i}
            className="page-tab editing"
            defaultValue={page.name}
            aria-label="Page name"
            autoFocus
            maxLength={24}
            onBlur={(e) => {
              controller.renamePage(i, e.currentTarget.value);
              setEditing(null);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") e.currentTarget.blur();
              if (e.key === "Escape") setEditing(null);
            }}
          />
        ) : (
          <button
            key={i}
            type="button"
            role="tab"
            aria-selected={i === active}
            className={i === active ? "page-tab active" : "page-tab"}
            title="Double-click to rename"
            onClick={() => void controller.switchTo(i)}
            onDoubleClick={() => setEditing(i)}
          >
            {page.name}
            {pages.length > 1 && i === active && (
              <span
                className="page-close"
                role="button"
                aria-label={`Close page ${page.name}`}
                title="Close this page"
                onClick={(e) => {
                  e.stopPropagation();
                  void controller.removePage(i);
                }}
              >
                ×
              </span>
            )}
          </button>
        ),
      )}
      <button type="button" className="page-add" aria-label="New page" title="New page" onClick={() => void controller.addPage()}>
        +
      </button>
      <span className="grow" />
      <span className="page-status">
        {workingCount} working · layout {saveLabel(controller.saveState)}
      </span>
      {children}
    </div>
  );
}

function saveLabel(state: WorkspaceController["saveState"]): string {
  switch (state) {
    case "saved":
      return "saved";
    case "saving":
      return "saving…";
    case "unsaved":
      return "changed";
    case "browser-only":
      return "saved in this browser only";
  }
}
