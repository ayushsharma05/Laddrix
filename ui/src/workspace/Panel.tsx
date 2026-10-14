// The frame around every widget inside dockview: hands the widget its saved params, saves
// changes, follows the link group, and keeps the tab title in step.

import type { IDockviewPanelHeaderProps, IDockviewPanelProps } from "dockview-react";
import { createContext, useCallback, useContext, useEffect, useRef, type FunctionComponent } from "react";
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
  const { links } = useApp();
  const controller = useController();
  const latest = useRef(params);
  latest.current = params;

  const setParams = useCallback(
    (change: Partial<BaseParams>) => {
      const next = { ...latest.current, ...change };
      latest.current = next;
      api.updateParameters(next);
      controller.changed();
    },
    [api, controller],
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

  // keep the tab title in step with the params
  const title = def ? (def.tabTitle?.(params) ?? def.title) : kind;
  useEffect(() => {
    if (api.title !== title) api.setTitle(title);
  }, [api, title]);

  if (!def) return <div className="empty-note">This widget ({kind}) isn't available in this version.</div>;
  const Component = def.component;
  return (
    <div className="widget" data-link={link}>
      <Component params={params} setParams={setParams} setInstrument={setInstrument} publish={publish} open={open} />
    </div>
  );
}

/** The tab: link colour (click to change group), title, close. Acts as the window title bar. */
export function WidgetTab({ api, params }: IDockviewPanelHeaderProps<BaseParams>) {
  const controller = useController();
  const def = widget(api.component);
  const link: LinkGroup = params?.link ?? "none";
  const cycle = (e: React.MouseEvent) => {
    e.stopPropagation();
    api.updateParameters({ ...params, link: nextLink(link) });
    controller.changed();
  };
  return (
    <div className="tab">
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
      <span className="tab-title">{api.title}</span>
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
