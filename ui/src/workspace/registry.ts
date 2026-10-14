// Every kind of widget the workspace can open. A widget is a React component that gets
// its settings as `params` (saved with the layout) and can change them with `setParams`.
// Widgets never reach into the workspace: that keeps them movable to their own window later.

import type { FunctionComponent } from "react";
import type { LinkGroup } from "./links";

export interface BaseParams {
  link: LinkGroup;
  instrumentId?: string;
}

export interface WidgetProps<P extends BaseParams = BaseParams> {
  params: P;
  /** Change this widget's saved settings. */
  setParams: (change: Partial<P>) => void;
  /** Switch instrument and tell the other widgets in the same link group. */
  setInstrument: (instrumentId: string) => void;
  /** Tell the link group about an instrument without switching this widget (watch lists). */
  publish: (instrumentId: string) => void;
  /** Open another widget next to this one (e.g. a ladder from a watch list). */
  open: (kind: string, params: Partial<BaseParams> & Record<string, unknown>) => void;
}

/**
 * How a widget uses link groups:
 * - "follows": shows one instrument and follows its group (ladder, ticket)
 * - "drives": picks instruments for its group but doesn't follow (watch list)
 * - "none": not linked (order book, messages)
 */
export type LinkRole = "follows" | "drives" | "none";

export interface WidgetDef<P extends BaseParams = BaseParams> {
  kind: string;
  title: string; // shown in the Widgets menu and the tab
  description: string; // one line for the Widgets menu
  component: FunctionComponent<WidgetProps<P>>;
  defaults: () => P;
  link: LinkRole;
  /** Tab text, e.g. "NIFTY26OCTFUT" for a ladder. */
  tabTitle?: (params: P) => string | undefined;
  size?: { width?: number; height?: number };
}

const widgets = new Map<string, WidgetDef<BaseParams>>();

export function registerWidget<P extends BaseParams>(def: WidgetDef<P>): void {
  widgets.set(def.kind, def as unknown as WidgetDef<BaseParams>);
}

export function widget(kind: string): WidgetDef<BaseParams> | undefined {
  return widgets.get(kind);
}

export function allWidgets(): WidgetDef<BaseParams>[] {
  return [...widgets.values()];
}
