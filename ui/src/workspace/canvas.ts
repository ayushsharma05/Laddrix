// A free page: widgets are windows on a canvas, each with its own place and size, with
// empty space allowed between them. The canvas is bigger than the screen when windows sit
// past its edge, and the page scrolls both ways to reach them (#410).
//
//   drag or resize ─► snapMove / snapResize ─► an edge near a neighbour's edge (8 px) sticks
//                                           └► otherwise the box lands on the 8 px grid
//                     (Alt held: no snapping, the box goes exactly where the mouse is)
//
// Pure functions only: the canvas component and the controller both use them, and the tests
// check them without a browser.

import type { BaseParams } from "./registry";

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** One widget on a free page. */
export interface CanvasWindow extends Box {
  id: string;
  kind: string;
  params: BaseParams & Record<string, unknown>;
  z: number; // higher is in front
}

export interface Canvas {
  windows: CanvasWindow[];
}

export const GRID = 8; // boxes land on multiples of this
export const SNAP = 8; // an edge this close to a neighbour's edge sticks to it
export const GAP = 4; // the space left between two windows snapped side by side
export const MIN_W = 160; // small enough for a tiny window, big enough for its title bar
export const MIN_H = 80;
export const MAX_SIDE = 8000; // a window bigger than any screen is a mistake
export const MAX_POS = 20000; // how far out a window can go
export const MAX_WINDOWS = 200; // per page: "any number", kept under the server's 256 KB per layout
export const DEFAULT_SIZE = { w: 420, h: 320 };
export const ROOM = 240; // empty canvas past the last window, so there is room to drag into

const finite = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n);

function isWindow(value: unknown): value is CanvasWindow {
  if (!value || typeof value !== "object") return false;
  const w = value as Partial<CanvasWindow>;
  return (
    typeof w.id === "string" &&
    w.id.length > 0 &&
    typeof w.kind === "string" &&
    !!w.params &&
    typeof w.params === "object" &&
    !Array.isArray(w.params) &&
    [w.x, w.y, w.w, w.h, w.z].every(finite)
  );
}

export function isCanvas(value: unknown): value is Canvas {
  if (!value || typeof value !== "object") return false;
  const windows = (value as Partial<Canvas>).windows;
  if (!Array.isArray(windows) || windows.length > MAX_WINDOWS || !windows.every(isWindow)) return false;
  return new Set(windows.map((w) => w.id)).size === windows.length; // ids name one window each
}

const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, n));

/** A box made sane: on the canvas, not too small or too big, whole pixels. */
export function tidy(box: Box): Box {
  const w = clamp(Math.round(box.w), MIN_W, MAX_SIDE);
  const h = clamp(Math.round(box.h), MIN_H, MAX_SIDE);
  return { x: clamp(Math.round(box.x), 0, MAX_POS), y: clamp(Math.round(box.y), 0, MAX_POS), w, h };
}

/**
 * The nearest place an edge can stick to, within SNAP, or null.
 * `edge` is where the edge is now; `targets` are where it may stick.
 */
function nearest(edge: number, targets: number[]): number | null {
  let best: number | null = null;
  for (const t of targets) {
    const d = Math.abs(t - edge);
    if (d <= SNAP && (best === null || d < Math.abs(best - edge))) best = t;
  }
  return best;
}

const toGrid = (n: number) => Math.round(n / GRID) * GRID;

/** Where a leading edge (left or top) may stick: neighbours' same edges, just past their far edges, the canvas edge. */
function leadTargets(others: Box[], axis: "x" | "y"): number[] {
  const size = axis === "x" ? "w" : "h";
  return [0, ...others.flatMap((o) => [o[axis], o[axis] + o[size] + GAP])];
}

/** Where a trailing edge (right or bottom) may stick: neighbours' same edges, just before their near edges. */
function trailTargets(others: Box[], axis: "x" | "y"): number[] {
  const size = axis === "x" ? "w" : "h";
  return others.flatMap((o) => [o[axis] + o[size], o[axis] - GAP]);
}

/** Where a dragged window lands: an edge near a neighbour's sticks to it, else the grid. */
export function snapMove(box: Box, others: Box[], free = false): Box {
  if (free) return tidy(box);
  const place = (axis: "x" | "y") => {
    const size = axis === "x" ? box.w : box.h;
    const lead = nearest(box[axis], leadTargets(others, axis));
    const trail = nearest(box[axis] + size, trailTargets(others, axis));
    // the closer of the two edges wins; neither near anything: the grid
    if (lead !== null && (trail === null || Math.abs(lead - box[axis]) <= Math.abs(trail - size - box[axis]))) return lead;
    if (trail !== null) return trail - size;
    return toGrid(box[axis]);
  };
  return tidy({ ...box, x: place("x"), y: place("y") });
}

/** Which edges a resize handle moves. */
export interface Edges {
  left?: boolean;
  right?: boolean;
  top?: boolean;
  bottom?: boolean;
}

/**
 * The box after dragging a resize handle by (dx, dy) from `start`. Only the moved edges
 * change (and snap); the opposite edges stay where they were.
 */
export function snapResize(start: Box, dx: number, dy: number, edges: Edges, others: Box[], free = false): Box {
  const stick = (edge: number, targets: number[]) => (free ? Math.round(edge) : (nearest(edge, targets) ?? toGrid(edge)));
  let { x, y } = start;
  let right = start.x + start.w;
  let bottom = start.y + start.h;
  if (edges.left) x = clamp(stick(start.x + dx, leadTargets(others, "x")), 0, right - MIN_W);
  if (edges.right) right = stick(right + dx, trailTargets(others, "x"));
  if (edges.top) y = clamp(stick(start.y + dy, leadTargets(others, "y")), 0, bottom - MIN_H);
  if (edges.bottom) bottom = stick(bottom + dy, trailTargets(others, "y"));
  return tidy({ x, y, w: right - x, h: bottom - y }); // tidy keeps it at least MIN_W × MIN_H
}

const overlaps = (a: Box, b: Box) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

/**
 * Where a new window goes: beside the window it was opened from (ladders to the right,
 * grids below) if that spot is empty and on screen, else the first empty spot on screen,
 * else a step down and right of the screen's corner. `view` is the part of the canvas on screen.
 */
export function placeNew(
  windows: Box[],
  size: { w: number; h: number },
  view: Box,
  from?: Box,
  direction: "right" | "below" = "right",
): Box {
  const w = clamp(size.w, MIN_W, MAX_SIDE);
  const h = clamp(size.h, MIN_H, MAX_SIDE);
  const free = (b: Box) => !windows.some((o) => overlaps(b, o));
  const tries: Box[] = [];
  if (from) {
    const right = { x: from.x + from.w + GAP, y: from.y, w, h };
    const below = { x: from.x, y: from.y + from.h + GAP, w, h };
    tries.push(...(direction === "right" ? [right, below] : [below, right]));
  }
  for (const b of tries) if (free(b) && overlaps(b, view)) return tidy(b);
  const step = GRID * 4;
  for (let y = toGrid(view.y + GRID); y + h <= view.y + view.h; y += step) {
    for (let x = toGrid(view.x + GRID); x + w <= view.x + view.w; x += step) {
      const b = { x, y, w, h };
      if (free(b)) return b;
    }
  }
  // the view is full: cascade from its corner so the new window is seen and isn't hidden exactly under another
  let b = { x: toGrid(view.x + GRID * 3), y: toGrid(view.y + GRID * 3), w, h };
  while (windows.some((o) => o.x === b.x && o.y === b.y)) b = { ...b, x: b.x + GRID * 3, y: b.y + GRID * 3 };
  return tidy(b);
}

/** How big the canvas is: the screen, or further when windows sit past it (plus room to drag into). */
export function extent(windows: Box[], view: { w: number; h: number }): { w: number; h: number } {
  let w = view.w;
  let h = view.h;
  for (const o of windows) {
    w = Math.max(w, o.x + o.w + ROOM);
    h = Math.max(h, o.y + o.h + ROOM);
  }
  return { w, h };
}

/** The windows with `id` brought in front of the rest (z numbers kept small). */
export function toFront(windows: CanvasWindow[], id: string): CanvasWindow[] {
  const top = Math.max(0, ...windows.map((w) => w.z));
  const target = windows.find((w) => w.id === id);
  if (!target || (target.z === top && windows.filter((w) => w.z === top).length === 1)) return windows;
  const order = [...windows].sort((a, b) => a.z - b.z).filter((w) => w.id !== id);
  const z = new Map(order.map((w, i) => [w.id, i + 1]));
  z.set(id, order.length + 1);
  return windows.map((w) => ({ ...w, z: z.get(w.id)! }));
}

/** The z for a window added on top. */
export function topZ(windows: CanvasWindow[]): number {
  return Math.max(0, ...windows.map((w) => w.z)) + 1;
}
