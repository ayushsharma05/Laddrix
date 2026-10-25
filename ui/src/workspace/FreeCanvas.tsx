// A free page (#410): every widget is a window you drag by its title bar and resize from any
// edge or corner. Windows can sit anywhere, with gaps between them, and past the screen's edge:
// the page then scrolls, both ways, to reach them.
//
//   pointer down on a title bar ─► follow the pointer ─► snapMove (Alt: no snapping)
//   pointer down on an edge      ─► follow the pointer ─► snapResize (Alt: no snapping)
//   pointer up                   ─► controller.moveWindow ─► saved like any other change
//
// While a window moves only this component redraws it; the controller hears once, at the end.

import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { GRID, extent, snapMove, snapResize, tidy, type Box, type CanvasWindow, type Edges } from "./canvas";
import type { WorkspaceController } from "./controller";
import { LINK_COLOURS, nextLink, type LinkGroup } from "./links";
import { WidgetBody } from "./Panel";
import { widget, type BaseParams } from "./registry";

/** The eight resize handles: which edges each one moves. */
const HANDLES: { name: string; edges: Edges }[] = [
  { name: "n", edges: { top: true } },
  { name: "s", edges: { bottom: true } },
  { name: "e", edges: { right: true } },
  { name: "w", edges: { left: true } },
  { name: "ne", edges: { top: true, right: true } },
  { name: "nw", edges: { top: true, left: true } },
  { name: "se", edges: { bottom: true, right: true } },
  { name: "sw", edges: { bottom: true, left: true } },
];

const NO_WINDOWS: CanvasWindow[] = []; // one empty list, the same every render
const EDGE_SCROLL = 32; // dragging this close to the page's edge scrolls it
const SCROLL_STEP = 24;

interface Live {
  id: string;
  box: Box;
}

export function FreeCanvas({ controller, empty }: { controller: WorkspaceController; empty: ReactNode }) {
  const scroller = useRef<HTMLDivElement>(null);
  const surface = useRef<HTMLDivElement>(null);
  const [view, setView] = useState<Box>({ x: 0, y: 0, w: 1200, h: 800 });
  const [live, setLive] = useState<Live | null>(null);
  const [maximized, setMaximized] = useState<string | null>(null);
  const windows = controller.canvas?.windows ?? NO_WINDOWS;

  // which part of the canvas is on screen (new windows are placed there)
  const readView = useCallback((): Box => {
    const el = scroller.current;
    if (!el || !el.clientWidth) return { x: 0, y: 0, w: 1200, h: 800 };
    return { x: el.scrollLeft, y: el.scrollTop, w: el.clientWidth, h: el.clientHeight };
  }, []);
  useEffect(() => controller.setViewport(readView), [controller, readView]);
  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const update = () => setView(readView());
    update();
    el.addEventListener("scroll", update, { passive: true });
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(update) : null;
    observer?.observe(el);
    return () => {
      el.removeEventListener("scroll", update);
      observer?.disconnect();
    };
  }, [readView]);

  // a window just added: scroll to it if it is out of sight
  const reveal = controller.reveal;
  useEffect(() => {
    const id = reveal ? controller.takeReveal() : null;
    if (!id) return;
    const w = controller.canvas?.windows.find((x) => x.id === id);
    const el = scroller.current;
    if (!w || !el) return;
    const v = readView();
    const seen = w.x >= v.x && w.y >= v.y && w.x + Math.min(w.w, v.w) <= v.x + v.w && w.y + Math.min(w.h, v.h) <= v.y + v.h;
    if (!seen) el.scrollTo?.({ left: Math.max(0, w.x - GRID * 2), top: Math.max(0, w.y - GRID * 2) });
  }, [controller, reveal, readView]);

  // a closed or gone window can't stay maximized, nor come back maximized (another page, and
  // back): forgotten while drawing, the moment it is gone
  if (maximized && !windows.some((w) => w.id === maximized)) setMaximized(null);
  const maxId = maximized && windows.some((w) => w.id === maximized) ? maximized : null;

  /** Scroll the page when the pointer is held near its edge during a drag. */
  const edgeScroll = useCallback((clientX: number, clientY: number) => {
    const el = scroller.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    if (clientX > r.right - EDGE_SCROLL) el.scrollLeft += SCROLL_STEP;
    else if (clientX < r.left + EDGE_SCROLL) el.scrollLeft -= SCROLL_STEP;
    if (clientY > r.bottom - EDGE_SCROLL) el.scrollTop += SCROLL_STEP;
    else if (clientY < r.top + EDGE_SCROLL) el.scrollTop -= SCROLL_STEP;
  }, []);

  const onCanvas = windows.filter((w) => !controller.popoutOf(w.id));
  const boxes = onCanvas.map((w) => (live?.id === w.id ? live.box : w));
  const size = extent(boxes, view);

  return (
    <div className="canvas-scroll" ref={scroller} data-testid="free-canvas">
      <div className="canvas" ref={surface} style={{ width: size.w, height: size.h }}>
        {onCanvas.map((w) => (
          <CanvasFrame
            key={w.id}
            win={w}
            controller={controller}
            locked={controller.locked}
            live={live?.id === w.id ? live.box : null}
            setLive={setLive}
            maxBox={maxId === w.id ? view : null}
            toggleMax={setMaximized}
            surface={surface}
            edgeScroll={edgeScroll}
          />
        ))}
      </div>
      {windows.length === 0 && empty}
      {windows
        .filter((w) => controller.popoutOf(w.id))
        .map((w) => (
          <PopoutPortal key={w.id} target={controller.popoutOf(w.id)!} title={titleOf(w)} onClosed={() => controller.dockBack(w.id)}>
            <div className="popout-frame" data-widget-id={w.id}>
              <TitleBar win={w} controller={controller} popped />
              <div className="frame-body">
                <FrameBody win={w} controller={controller} />
              </div>
            </div>
          </PopoutPortal>
        ))}
    </div>
  );
}

function titleOf(w: CanvasWindow): string {
  const def = widget(w.kind);
  return def ? (def.tabTitle?.(w.params) ?? def.title) : w.kind;
}

interface FrameProps {
  win: CanvasWindow;
  controller: WorkspaceController;
  locked: boolean; // passed in so a lock redraws the frame
  live: Box | null; // where it is while being dragged
  setLive: (live: Live | null) => void;
  maxBox: Box | null; // filling the visible page
  toggleMax: (fn: (current: string | null) => string | null) => void;
  surface: React.RefObject<HTMLDivElement | null>;
  edgeScroll: (clientX: number, clientY: number) => void;
}

const CanvasFrame = memo(function CanvasFrame({ win, controller, locked, live, setLive, maxBox, toggleMax, surface, edgeScroll }: FrameProps) {
  const box = maxBox ?? live ?? win;

  /** Follow the pointer from here until it is let go: moving the window, or one or more of its edges. */
  const follow = (e: React.PointerEvent, edges: Edges | null) => {
    if (e.button !== 0 || locked || maxBox) return;
    const el = surface.current;
    if (!el) return;
    e.preventDefault();
    e.stopPropagation();
    controller.focusWindow(win.id);
    const at = (ev: { clientX: number; clientY: number }) => {
      const r = el.getBoundingClientRect();
      return { x: ev.clientX - r.left, y: ev.clientY - r.top };
    };
    const start = at(e);
    const from: Box = { x: win.x, y: win.y, w: win.w, h: win.h };
    const others = (controller.canvas?.windows ?? []).filter((o) => o.id !== win.id && !controller.popoutOf(o.id));
    let last = from;
    const move = (ev: PointerEvent) => {
      edgeScroll(ev.clientX, ev.clientY);
      const p = at(ev);
      const [dx, dy] = [p.x - start.x, p.y - start.y];
      last = edges ? snapResize(from, dx, dy, edges, others, ev.altKey) : snapMove({ ...from, x: from.x + dx, y: from.y + dy }, others, ev.altKey);
      setLive({ id: win.id, box: last });
    };
    const done = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", done);
      window.removeEventListener("pointercancel", done);
      setLive(null);
      controller.moveWindow(win.id, last);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", done);
    window.addEventListener("pointercancel", done);
  };

  /** Arrow keys on a focused title bar: move by the grid (Alt: one pixel); with Shift, resize. */
  const onKey = (e: React.KeyboardEvent) => {
    const step = e.altKey ? 1 : GRID;
    const d = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] }[e.key];
    if (!d || locked || maxBox) return;
    e.preventDefault();
    const [dx, dy] = d as [number, number];
    const next = e.shiftKey ? { ...win, w: win.w + dx, h: win.h + dy } : { ...win, x: win.x + dx, y: win.y + dy };
    controller.moveWindow(win.id, tidy(next));
  };

  return (
    <section
      className={maxBox ? "frame maximized" : live ? "frame moving" : "frame"}
      style={{ left: box.x, top: box.y, width: box.w, height: box.h, zIndex: maxBox ? 100000 : win.z }}
      aria-label={titleOf(win)}
      data-widget-id={win.id}
      onPointerDownCapture={() => controller.focusWindow(win.id)}
    >
      <TitleBar
        win={win}
        controller={controller}
        locked={locked}
        onPointerDown={(e) => follow(e, null)}
        onKeyDown={onKey}
        onDoubleClick={() => toggleMax((current) => (current === win.id ? null : win.id))}
      />
      <div className="frame-body">
        <FrameBody win={win} controller={controller} />
      </div>
      {!locked &&
        !maxBox &&
        HANDLES.map((h) => <div key={h.name} className={`rz rz-${h.name}`} aria-hidden onPointerDown={(e) => follow(e, h.edges)} />)}
    </section>
  );
});

/** The widget itself: kept apart so moving the window doesn't redraw it. */
const FrameBody = memo(function FrameBody({ win, controller }: { win: CanvasWindow; controller: WorkspaceController }) {
  const update = useCallback((next: BaseParams) => controller.setWindowParams(win.id, next as CanvasWindow["params"]), [controller, win.id]);
  return <WidgetBody id={win.id} kind={win.kind} params={win.params} update={update} />;
}, (a, b) => a.win.params === b.win.params && a.win.kind === b.win.kind && a.win.id === b.win.id && a.controller === b.controller);

interface TitleBarProps {
  win: CanvasWindow;
  controller: WorkspaceController;
  locked?: boolean;
  popped?: boolean;
  onPointerDown?: (e: React.PointerEvent) => void;
  onKeyDown?: (e: React.KeyboardEvent) => void;
  onDoubleClick?: () => void;
}

/** The window's title bar: link colour (click to change group), title, pop out, close. */
function TitleBar({ win, controller, locked = controller.locked, popped, onPointerDown, onKeyDown, onDoubleClick }: TitleBarProps) {
  const def = widget(win.kind);
  const link: LinkGroup = win.params.link ?? "none";
  const stop = (e: React.PointerEvent | React.MouseEvent) => e.stopPropagation();
  return (
    <div
      className="tab frame-title"
      tabIndex={popped ? undefined : 0}
      title={popped ? undefined : "Drag to move (Alt: no snapping). Double-click to fill the page. Arrow keys move it, Shift+arrows resize."}
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
      onDoubleClick={onDoubleClick}
    >
      {def && def.link !== "none" && (
        <button
          type="button"
          className="tab-link"
          style={{ background: LINK_COLOURS[link] }}
          title={`Link group: ${link}. Click to change.`}
          aria-label={`Link group ${link}`}
          onPointerDown={stop}
          onClick={() => controller.setWindowParams(win.id, { ...win.params, link: nextLink(link) })}
        />
      )}
      <span className="tab-title">{titleOf(win)}</span>
      <span className="grow" />
      {popped ? (
        <button type="button" className="tab-close" aria-label="Dock back" title="Put back on the page" onPointerDown={stop} onClick={() => controller.dockBack(win.id)}>
          ⇲
        </button>
      ) : (
        <button
          type="button"
          className="tab-close"
          aria-label="Pop out"
          title="Pop out into its own window (e.g. another monitor)"
          onPointerDown={stop}
          onDoubleClick={stop}
          onClick={() => void controller.popOut(win.id)}
        >
          ⧉
        </button>
      )}
      <button
        type="button"
        className="tab-close"
        aria-label="Close"
        title="Close"
        disabled={locked}
        onPointerDown={stop}
        onDoubleClick={stop}
        onClick={() => controller.closeWindow(win.id)}
      >
        ×
      </button>
    </div>
  );
}

/**
 * Draws its children into another browser window (a popped-out widget). The window loads
 * popout.html, which runs no code: this copies the main window's styles and theme into it.
 * Closing the window puts the widget back on the page.
 */
function PopoutPortal({ target, title, onClosed, children }: { target: Window; title: string; onClosed: () => void; children: ReactNode }) {
  const [host, setHost] = useState<HTMLElement | null>(null);
  const closed = useRef(onClosed);
  useLayoutEffect(() => {
    closed.current = onClosed; // the newest, for the watcher set up once
  });
  useEffect(() => {
    let ready = false;
    const setup = () => {
      const doc = target.document;
      if (ready || !doc?.body) return;
      ready = true;
      for (const node of document.head.querySelectorAll('style, link[rel="stylesheet"]')) doc.head.appendChild(node.cloneNode(true));
      for (const name of ["data-theme", "style"]) {
        const value = document.documentElement.getAttribute(name);
        if (value !== null) doc.documentElement.setAttribute(name, value);
      }
      const el = doc.createElement("div");
      el.className = "popout-root";
      doc.body.appendChild(el);
      setHost(el);
    };
    target.addEventListener("load", setup);
    try {
      if (target.document?.readyState === "complete" && target.location.href !== "about:blank") setup();
    } catch {
      // not ready yet: the load event comes
    }
    const watch = setInterval(() => {
      if (target.closed) closed.current();
    }, 400);
    return () => {
      clearInterval(watch);
      target.removeEventListener("load", setup);
    };
  }, [target]);
  useEffect(() => {
    // eslint-disable-next-line react-hooks/immutability -- the popped-out browser window's own title: outside React, set from an effect as it should be
    if (host) host.ownerDocument.title = `${title} · MockTrade`;
  }, [host, title]);
  return host ? createPortal(children, host) : null;
}
