// Messages: every refusal, warning and notice, newest first (docs/design/ui.md §4). Ladders
// show the same events in their status line; this is the full list.

import { useMemo } from "react";
import { useApp, useStore } from "../../app/context";
import { clock, shortName } from "../../lib/format";
import type { Level } from "../../state/store";
import { registerWidget, type BaseParams, type WidgetProps } from "../../workspace/registry";

export interface MessagesParams extends BaseParams {
  level: "all" | "problems"; // problems = ERROR and WARN only
}

const LEVEL_TONE: Record<Level, string> = { ERROR: "bad", WARN: "pending", INFO: "buy", OK: "good" };

export function Messages({ params, setParams }: WidgetProps<MessagesParams>) {
  const { store } = useApp();
  const messages = useStore((s) => s.messages);
  const level = params.level ?? "all";
  const shown = useMemo(
    () => (level === "all" ? messages : messages.filter((m) => m.level === "ERROR" || m.level === "WARN")),
    [messages, level],
  );
  return (
    <div className="book">
      <div className="book-bar">
        <button type="button" className={`book-tab${level === "all" ? " on" : ""}`} aria-pressed={level === "all"} onClick={() => setParams({ level: "all" })}>
          All {messages.length}
        </button>
        <button
          type="button"
          className={`book-tab${level === "problems" ? " on" : ""}`}
          aria-pressed={level === "problems"}
          onClick={() => setParams({ level: "problems" })}
        >
          Problems {messages.filter((m) => m.level === "ERROR" || m.level === "WARN").length}
        </button>
        <span className="book-spacer" />
        <button type="button" className="btn small" disabled={!messages.length} onClick={() => store.clearMessages()}>
          Clear
        </button>
      </div>
      <div className="book-scroll">
        <div className="grid-head book-row messages-row" role="row">
          <span>Time</span>
          <span>Level</span>
          <span>Contract</span>
          <span>Message</span>
        </div>
        <div className="grid-body" role="grid" aria-label="Messages">
          {!shown.length && <div className="empty-note">Nothing yet.</div>}
          {shown.map((m) => (
            <div key={m.id} role="row" className="row book-row messages-row" title={m.text}>
              <span className="dim">{clock(new Date(m.at).toISOString(), false)}</span>
              <span className={LEVEL_TONE[m.level]}>{m.level}</span>
              <span>{m.instrumentId ? shortName(m.instrumentId) : ""}</span>
              <span>{m.text}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

registerWidget<MessagesParams>({
  kind: "messages",
  title: "Messages",
  description: "Every refusal, warning and notice",
  component: Messages,
  defaults: () => ({ link: "none", level: "all" }),
  link: "none",
});
