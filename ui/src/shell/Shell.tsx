// The terminal around the widgets:
//
//   menu bar     Laddrix · account ▾                        user · Sessions · Log out
//   toolbar      Widgets ▾ · Search (/) ······ lights · rate · Day P&L · Layouts ▾ · clock · Save · Lock · STOP
//   env strip    PAPER · orders fill against live prices, nothing goes to the exchange
//   bands        (only when needed)
//   desktop      the page: free windows or docked tiles (#410)
//   page tabs    Main | + ……………………………… working count · layout saved · version

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { useApp, useKeyBindings, useNow, useStore, useThemeChoice } from "../app/context";
import { ContextMenu, type MenuItem } from "../components/ContextMenu";
import { actionFor, chordOf } from "../keys/model";
import { Menu } from "../components/Menu";
import { SearchBox } from "../components/SearchBox";
import { clock, rupees } from "../lib/format";
import { browserStorage, LayoutStore } from "../workspace/layouts";
import { WorkspaceController } from "../workspace/controller";
import { buildDefault, defaultCanvas } from "../workspace/defaults";
import { pageMenu, widgetMenu } from "../workspace/menus";
import { widgetsFor } from "../workspace/registry";
import { THEME_CHOICES, type ThemeChoice } from "../theme/theme";
import { Workspace } from "../workspace/Workspace";
import { Bands, PRICES_OLD_MS } from "./Bands";
import { EnvStrip } from "./EnvStrip";
import { ReplayBar } from "./ReplayBar";
import { ServerVersion } from "./ServerVersion";
import { SessionsDialog } from "./SessionsDialog";
import { StepUpDialog } from "./StepUpDialog";
import { StopDialog } from "./StopDialog";
import { ThemePick } from "./ThemePick";

export const RATE_LIMIT_PER_MIN = 350; // the platform's own per-minute order limit (risk/throttle.py)

export function Shell() {
  const app = useApp();
  const me = useStore((s) => s.me)!;
  const roles = me.user.roles;
  const controller = useMemo(
    () =>
      new WorkspaceController({
        layouts: new LayoutStore(app.rest, browserStorage(), me.user.id),
        buildDefault: (api, page) => buildDefault(api, page, app.instruments),
        buildDefaultCanvas: (page) => defaultCanvas(page, app.instruments),
        notify: (level, text) => app.store.addMessage(level, "", text),
      }),
    [app, me.user.id],
  );
  useEffect(() => () => controller.dispose(), [controller]);
  const [dialog, setDialog] = useState<"stop" | "resume" | null>(null);
  const search = useRef<HTMLInputElement>(null);
  const stopped = useStore((s) => Boolean(s.accountId && s.books[s.accountId]?.kill.engaged));

  // back from the broker's own login page (/kite/callback redirects to /?broker_login=…)
  useEffect(() => {
    const url = new URL(window.location.href);
    const result = url.searchParams.get("broker_login");
    if (!result) return;
    if (result === "ok") app.store.addMessage("OK", "", "Logged in to the broker for today");
    else app.store.addMessage("ERROR", "", "Broker login failed: try again from the account strip");
    url.searchParams.delete("broker_login");
    window.history.replaceState(null, "", url.pathname + url.search + url.hash);
  }, [app]);

  // keys that work anywhere (K13.4: the user's choices, else the defaults; STOP always has
  // Ctrl+Shift+K): search, STOP, save the layout, open a ticket / ladder / chart
  useEffect(() => void app.keys.load(), [app]);
  useEffect(() => void app.theme.load(), [app]);
  const bindings = useKeyBindings();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.target instanceof HTMLSelectElement;
      const chord = chordOf(e);
      if (!chord || e.defaultPrevented) return; // a widget already took it
      const plain = !/^(Ctrl|Alt)\+/.test(chord) && !/^F\d+$/.test(chord);
      if (typing && plain) return; // typing "/" in a box is just a slash
      const action = actionFor(chord, "global", bindings);
      if (!action) return;
      e.preventDefault();
      if (action === "search") search.current?.focus();
      else if (action === "stop") setDialog("stop");
      else if (action === "save-layout") void controller.saveNow();
      else if (action === "open-ticket") controller.addWidget("ticket");
      else if (action === "open-ladder") controller.addWidget("ladder");
      else if (action === "open-chart") controller.addWidget("chart");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [controller, bindings]);

  const menu = useRightClickMenu(controller, roles, () => search.current?.focus());

  return (
    <div className="terminal">
      {menu}
      <MenuBar />
      <div className="toolbar">
        <Menu label="Widgets ▾">
          {(close) =>
            widgetsFor(roles).map((w) => (
              <button
                key={w.kind}
                type="button"
                role="menuitem"
                className="menu-item"
                onClick={() => {
                  controller.addWidget(w.kind);
                  close();
                }}
              >
                <span className="strong">{w.title}</span>
                <span className="dim">{w.description}</span>
              </button>
            ))
          }
        </Menu>
        <SearchBox
          ref={search}
          className="toolbar-search"
          placeholder="Search product  ( / )"
          onPick={(i) => {
            // the pink ladders and tickets switch to it; with none open, open a ladder
            if (app.links.hasFollowers("pink")) app.links.publish("pink", i.id);
            else controller.addWidget("ladder", { instrumentId: i.id, link: "pink" });
          }}
        />
        <span className="grow" />
        <StatusLights />
        <span className="sep" />
        <DayPnl />
        <LayoutsMenu controller={controller} />
        <Clock />
        <button type="button" className="btn" title="Save the layout now (Ctrl+S)" onClick={() => void controller.saveNow()}>
          Save
        </button>
        <LockButton controller={controller} />
        {stopped ? (
          <button type="button" className="btn stopped-btn" title="Trading is stopped: click to resume" onClick={() => setDialog("resume")}>
            STOPPED
          </button>
        ) : (
          <button type="button" className="btn stop" title="Stop trading (Ctrl+Shift+K)" onClick={() => setDialog("stop")}>
            STOP
          </button>
        )}
      </div>
      <EnvStrip />
      <ReplayBar />
      <Bands />
      <Workspace controller={controller} footer={<ServerVersion />} />
      {dialog && <StopDialog mode={dialog} onClose={() => setDialog(null)} />}
      <StepUpDialog />
    </div>
  );
}

function MenuBar() {
  const { session, store } = useApp();
  const me = useStore((s) => s.me)!;
  const accountId = useStore((s) => s.accountId);
  const [sessions, setSessions] = useState(false);
  return (
    <nav className="menubar" aria-label="Menu">
      <span className="brand">
        <img className="brand-mark" src="favicon.svg" alt="" />
        Laddrix
      </span>
      <label className="account-pick">
        <span className="dim">Account</span>
        <select value={accountId ?? ""} onChange={(e) => store.selectAccount(e.target.value)} aria-label="Account for new orders">
          {me.accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.id} · {a.broker}
              {a.can_trade ? "" : " (view only)"}
            </option>
          ))}
        </select>
      </label>
      <span className="grow" />
      <ThemePick />
      <span className="dim">
        {me.user.name} · {me.user.roles.join(", ")}
      </span>
      <button type="button" className="link-btn" onClick={() => setSessions(true)}>
        Sessions
      </button>
      <button type="button" className="link-btn" onClick={() => void session.logout()}>
        Log out
      </button>
      {sessions && <SessionsDialog onClose={() => setSessions(false)} />}
    </nav>
  );
}

function StatusLights() {
  const { stream, store } = useApp();
  const now = useNow(1000);
  const status = useStore((s) => s.stream);
  const reconcile = useStore((s) => (s.accountId ? s.books[s.accountId]?.reconcile : null));
  const alerts = useStore((s) => (s.accountId ? s.books[s.accountId]?.alerts : undefined));
  useStore((s) => s.sentTimes);

  const server = status === "open" ? "good" : "bad";
  const age = stream.lastTickAt === null ? null : now - stream.lastTickAt;
  const prices = status !== "open" ? "bad" : age === null ? "off" : age > PRICES_OLD_MS ? "pending" : "good";
  const recon =
    alerts && Object.keys(alerts).length > 0 ? "bad" : reconcile?.ok === true ? "good" : reconcile?.ok === false ? "pending" : "off";
  const sent = store.sentLastMinute();
  return (
    <span className="lights">
      <Light state={server} label="Server" title={`Connection to Laddrix: ${status}`} />
      <Light
        state={prices}
        label="Prices"
        title={age === null ? "No prices yet" : `Last price ${Math.round(age / 1000)} s ago`}
      />
      <Light
        state={recon}
        label="Recon"
        title={
          reconcile?.checked_at
            ? `Positions checked against the broker at ${clock(reconcile.checked_at, false)}${reconcile.error ? `: ${reconcile.error}` : ""}`
            : "Positions not checked against the broker yet"
        }
      />
      <span className={`rate${sent > RATE_LIMIT_PER_MIN * 0.8 ? " pending" : ""}`} title="Orders sent in the last minute / the limit">
        {sent}/{RATE_LIMIT_PER_MIN}·min
      </span>
    </span>
  );
}

function Light({ state, label, title }: { state: string; label: string; title: string }) {
  return (
    <span className="light" title={title}>
      <span className={`dot ${state}`} aria-hidden="true" />
      {label}
    </span>
  );
}

function DayPnl() {
  const pnl = useStore((s) => (s.accountId ? (s.books[s.accountId]?.dayPnl ?? 0) : 0));
  return (
    <span className="day-pnl" title="Today's profit and loss on this account (realised + unrealised)">
      Day <b className={pnl > 0 ? "good" : pnl < 0 ? "bad" : ""}>{rupees(pnl, { sign: true })}</b>
    </span>
  );
}

function Clock() {
  const now = useNow(1000);
  return (
    <span className="clock" title="India time">
      {clock(new Date(now).toISOString(), false)}
    </span>
  );
}

/**
 * The app's own right-click menu (#412), in this window and in every pop-out window: a widget's
 * menu on a widget, the page's menu anywhere else. Text boxes keep the browser's menu (copy,
 * paste), and Shift+right-click still gives the browser's own.
 */
function useRightClickMenu(controller: WorkspaceController, roles: string[], focusSearch: () => void) {
  const app = useApp();
  const theme = useThemeChoice();
  const [menu, setMenu] = useState<{ x: number; y: number; items: MenuItem[]; doc: Document } | null>(null);
  const latest = useRef({ theme, roles, focusSearch });
  useLayoutEffect(() => {
    latest.current = { theme, roles, focusSearch }; // the newest, for the listener set up once
  });

  const onMenu = useCallback(
    (e: MouseEvent) => {
      if (e.shiftKey || e.defaultPrevented) return;
      const target = e.target as Element | null;
      if (!target?.closest || target.closest("input, textarea, select, [contenteditable='true']")) return;
      e.preventDefault();
      const { roles, focusSearch, theme } = latest.current;
      const widgets = widgetsFor(roles);
      const host = target.closest<HTMLElement>("[data-widget-id]");
      let items = host ? widgetMenu({ controller, id: host.dataset.widgetId!, widgets }) : null;
      if (!items) {
        const surface = target.closest(".canvas");
        const r = surface?.getBoundingClientRect();
        items = pageMenu({
          controller,
          widgets,
          at: r ? { x: e.clientX - r.left, y: e.clientY - r.top } : undefined,
          focusSearch,
          themes: THEME_CHOICES,
          theme,
          chooseTheme: (value) => void app.theme.choose(value as ThemeChoice).catch(() => app.store.addMessage("WARN", "", "The theme wasn't saved")),
        });
      }
      // the keyboard's menu key: under the focused element, not at (0, 0)
      let { clientX: x, clientY: y } = e;
      if (x === 0 && y === 0 && target.getBoundingClientRect) {
        const r = target.getBoundingClientRect();
        [x, y] = [r.left + 8, r.bottom];
      }
      setMenu({ x, y, items, doc: target.ownerDocument });
    },
    [app, controller],
  );

  useEffect(() => {
    document.addEventListener("contextmenu", onMenu);
    const windows: Window[] = [];
    const off = controller.onWindow((w) => {
      windows.push(w);
      const add = () => w.document.addEventListener("contextmenu", onMenu);
      add();
      w.addEventListener("load", add); // the window's page replaces its first blank one
    });
    return () => {
      off();
      document.removeEventListener("contextmenu", onMenu);
      for (const w of windows) {
        try {
          w.document.removeEventListener("contextmenu", onMenu);
        } catch {
          // closed
        }
      }
    };
  }, [controller, onMenu]);

  if (!menu || !menu.doc.body) return null;
  return createPortal(<ContextMenu x={menu.x} y={menu.y} items={menu.items} onClose={() => setMenu(null)} />, menu.doc.body);
}

function LockButton({ controller }: { controller: WorkspaceController }) {
  useSyncExternalStore(controller.subscribe, controller.getVersion);
  const locked = controller.locked; // the right-click menu can lock it too
  return (
    <button
      type="button"
      className={locked ? "btn on" : "btn"}
      aria-pressed={locked}
      title={locked ? "Layout locked: widgets can't be moved or closed" : "Lock the layout"}
      onClick={() => controller.setLocked(!locked)}
    >
      {locked ? "Locked" : "Lock"}
    </button>
  );
}

function LayoutsMenu({ controller }: { controller: WorkspaceController }) {
  const { store } = useApp();
  const [names, setNames] = useState<string[] | null>(null);
  const [name, setName] = useState("");
  const layouts = controller.layouts;
  const refresh = () => {
    setNames(null);
    layouts.list().then(setNames, () => setNames([]));
  };
  return (
    <Menu label="Layouts ▾" align="right" onOpen={refresh}>
      {(close) => (
        <div className="layouts-menu">
          <form
            className="row-form"
            onSubmit={(e) => {
              e.preventDefault();
              void controller.saveAs(name).then((ok) => {
                if (ok) {
                  setName("");
                  refresh();
                }
              });
            }}
          >
            <input
              className="input"
              placeholder="Save as…"
              aria-label="Layout name"
              maxLength={64}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <button type="submit" className="btn">
              Save
            </button>
          </form>
          <div className="menu-label">Saved layouts</div>
          {names === null && <div className="menu-note">Loading…</div>}
          {names?.length === 0 && <div className="menu-note">None yet</div>}
          {names?.map((n) => (
            <div key={n} className="menu-item split">
              <button
                type="button"
                role="menuitem"
                className="plain grow"
                onClick={() => {
                  close();
                  void controller.open(n);
                }}
              >
                {n}
              </button>
              <button
                type="button"
                className="plain dim"
                aria-label={`Delete layout ${n}`}
                title="Delete"
                onClick={() =>
                  void layouts.remove(n).then(refresh, (err: Error) => store.addMessage("ERROR", "", err.message))
                }
              >
                ×
              </button>
            </div>
          ))}
          <div className="menu-sep" />
          <button
            type="button"
            role="menuitem"
            className="menu-item"
            onClick={() => {
              close();
              void controller.reset();
            }}
          >
            Reset to the starter layout
          </button>
        </div>
      )}
    </Menu>
  );
}
