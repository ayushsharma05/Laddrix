// The frame around every widget: hands the widget its saved params, saves changes, follows the
// link group (WidgetBody, shared with free windows), and for docked tiles keeps the tab title
// in step.

import type { IDockviewPanelHeaderProps, IDockviewPanelProps } from "dockview-react";
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState, type FunctionComponent } from "react";
import { useApp } from "../app/context";
import type { WorkspaceController } from "./controller";
import { LINK_COLOURS, nextLink, type LinkGroup } from "./links";
import { widget, type BaseParams } from "./registry";

export const ControllerContext = createContext<WorkspaceController | null>(null);

export function useController(): WorkspaceController {
  const c = useContext(ControllerContext);
  if (!c) throw new Error("useController outside the workspace");
  return c;
}

const panelCache = new Map<string, FunctionComponent<IDockviewPanelProps<BaseParams>>>();

/** The dockview component for a widget kind (one per kind, made once). */
export function panelFor(kind: string): FunctionComponent<IDockviewPanelProps<BaseParams>> {
  let panel = panelCache.get(kind);
  if (!panel) {
    panel = function WidgetPanel(props: IDockviewPanelProps<BaseParams>) {
      return <PanelBody kind={kind} {...props} />;
    };
    panelCache.set(kind, panel);
  }
  return panel;
}

function PanelBody({ kind, params, api }: IDockviewPanelProps<BaseParams> & { kind: string }) {
  const def = widget(kind);
  const controller = useController();
  const update = useCallback(
    (next: BaseParams) => {
      api.updateParameters(next);
      controller.changed();
    },
    [api, controller],
  );

  // keep the tab title in step with the params
  const title = def ? (def.tabTitle?.(params) ?? def.title) : kind;
  useEffect(() => {
    if (api.title !== title) api.setTitle(title);
  }, [api, title]);

  return <WidgetBody id={api.id} kind={kind} params={params} update={update} />;
}

/**
 * A widget with its settings: hands it its saved params, saves changes (through `update`),
 * and follows its link group. Used by docked tiles and free windows alike.
 */
export function WidgetBody({ id, kind, params, update }: { id: string; kind: string; params: BaseParams; update: (next: BaseParams) => void }) {
  const def = widget(kind);
  const { links } = useApp();
  const controller = useController();
  const latest = useRef(params);
  useLayoutEffect(() => {
    latest.current = params; // the saved params; two changes in a row build on each other
  });

  const setParams = useCallback(
    (change: Partial<BaseParams>) => {
      const next = { ...latest.current, ...change };
      latest.current = next;
      update(next);
    },
    [update],
  );

  const setInstrument = useCallback(
    (instrumentId: string) => {
      setParams({ instrumentId });
      links.publish(latest.current.link, instrumentId);
    },
    [links, setParams],
  );

  const publish = useCallback(
    (instrumentId: string) => links.publish(latest.current.link, instrumentId),
    [links],
  );

  const open = useCallback(
    (kind: string, extra: Partial<BaseParams> & Record<string, unknown>) => controller.addWidget(kind, extra),
    [controller],
  );

  // follow the link group (only widgets that show one instrument)
  const link = params.link ?? "none";
  useEffect(() => {
    if (def?.link !== "follows") return;
    return links.subscribe(link, (instrumentId) => {
      if (latest.current.instrumentId !== instrumentId) setParams({ instrumentId });
    });
  }, [def, link, links, setParams]);

  // data-widget-id: a right-click inside finds its widget (#412)
  if (!def) return <div className="empty-note" data-widget-id={id}>This widget ({kind}) isn't available in this version.</div>;
  const Component = def.component;
  return (
    <div className="widget" data-link={link} data-widget-id={id}>
      <Component params={params} setParams={setParams} setInstrument={setInstrument} publish={publish} open={open} />
    </div>
  );
}

/** "grid", "floating" or "popout", kept current as the widget moves between windows. */
function useLocation(api: IDockviewPanelHeaderProps<BaseParams>["api"]): string {
  const [where, setWhere] = useState(() => api.group.api.location.type);
  useEffect(() => {
    const update = () => setWhere(api.group.api.location.type);
    update();
    const subs = [api.onDidLocationChange(update), api.onDidGroupChange(update)];
    return () => subs.forEach((d) => d.dispose());
  }, [api]);
  return where;
}

/** The panel's title, kept current (a linked widget renames its tab when it switches). */
function useTitle(api: IDockviewPanelHeaderProps<BaseParams>["api"]): string {
  // kept with the panel it is for (another panel starts from its own title)
  const [seen, setSeen] = useState({ api, title: api.title ?? "" });
  useEffect(() => {
    const sub = api.onDidTitleChange((e) => setSeen({ api, title: e.title }));
    return () => sub.dispose();
  }, [api]);
  return seen.api === api ? seen.title : (api.title ?? "");
}

/** The tab: link colour (click to change group), title, close. Acts as the window title bar. */
export function WidgetTab({ api, params }: IDockviewPanelHeaderProps<BaseParams>) {
  const controller = useController();
  const where = useLocation(api);
  const title = useTitle(api);
  const def = widget(api.component);
  const link: LinkGroup = params?.link ?? "none";
  const cycle = (e: React.MouseEvent) => {
    e.stopPropagation();
    api.updateParameters({ ...params, link: nextLink(link) });
    controller.changed();
  };
  // double-click a tab: fill the workspace with this widget; again to put it back
  const toggleMaximized = () => {
    if (where === "popout") return;
    if (api.isMaximized()) api.exitMaximized();
    else api.maximize();
  };
  return (
    <div className="tab" data-widget-id={api.id} onDoubleClick={toggleMaximized} title="Double-click to maximize or restore">
      {def && def.link !== "none" && (
        <button
          type="button"
          className="tab-link"
          style={{ background: LINK_COLOURS[link] }}
          title={`Link group: ${link}. Click to change.`}
          aria-label={`Link group ${link}`}
          onClick={cycle}
          onMouseDown={(e) => e.stopPropagation()}
        />
      )}
      <span className="tab-title">{title}</span>
      {where === "popout" ? (
        <button
          type="button"
          className="tab-close"
          aria-label="Dock back"
          title="Put back into the workspace"
          onClick={(e) => {
            e.stopPropagation();
            controller.dockBack(api.id);
          }}
          onMouseDown={(e) => e.stopPropagation()}
        >
          ⇲
        </button>
      ) : (
        <button
          type="button"
          className="tab-close"
          aria-label="Pop out"
          title="Pop out into its own window (e.g. another monitor)"
          onClick={(e) => {
            e.stopPropagation();
            void controller.popOut(api.id);
          }}
          onMouseDown={(e) => e.stopPropagation()}
        >
          ⧉
        </button>
      )}
      <button
        type="button"
        className="tab-close"
        aria-label="Close"
        title="Close"
        onClick={(e) => {
          e.stopPropagation();
          if (!controller.locked) api.close();
        }}
        onMouseDown={(e) => e.stopPropagation()}
      >
        ×
      </button>
    </div>
  );
}
