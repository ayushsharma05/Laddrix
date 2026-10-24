import { describe, expect, it } from "vitest";
import {
  MAX_WINDOWS,
  MIN_H,
  MIN_W,
  ROOM,
  extent,
  isCanvas,
  placeNew,
  snapMove,
  snapResize,
  tidy,
  toFront,
  type CanvasWindow,
} from "./canvas";

const W = (id: string, x: number, y: number, w: number, h: number, z = 1): CanvasWindow => ({
  id,
  kind: "probe",
  params: { link: "none" },
  x,
  y,
  w,
  h,
  z,
});
const view = { x: 0, y: 0, w: 1200, h: 800 };

describe("snapping a dragged window", () => {
  const neighbour = { x: 100, y: 100, w: 300, h: 200 }; // right edge 400, bottom 300

  it("sticks just past a neighbour's edge, leaving a 4 px gap", () => {
    expect(snapMove({ x: 409, y: 103, w: 200, h: 150 }, [neighbour])).toMatchObject({ x: 404, y: 100 });
  });

  it("lines its right edge up with a neighbour's right edge", () => {
    expect(snapMove({ x: 205, y: 500, w: 200, h: 100 }, [neighbour])).toMatchObject({ x: 200 }); // 405 → 400
  });

  it("sits just before a neighbour's left edge", () => {
    expect(snapMove({ x: 100, y: 500, w: 205, h: 100 }, [{ x: 310, y: 0, w: 50, h: 50 }])).toMatchObject({ x: 101 }); // right 305 → 306
  });

  it("takes the nearer of two edges", () => {
    // the left edge is 2 px from 404, the right edge 6 px from 512: the left edge wins
    expect(snapMove({ x: 406, y: 600, w: 100, h: 100 }, [neighbour, { x: 0, y: 0, w: 512, h: 10 }])).toMatchObject({ x: 404 });
  });

  it("lands on the 8 px grid when nothing is near", () => {
    expect(snapMove({ x: 613, y: 701, w: 200, h: 100 }, [neighbour])).toMatchObject({ x: 616, y: 704 });
  });

  it("goes exactly where the mouse is with Alt held", () => {
    expect(snapMove({ x: 409.4, y: 103, w: 200, h: 150 }, [neighbour], true)).toMatchObject({ x: 409, y: 103 });
  });

  it("never leaves the canvas at the top or left", () => {
    expect(snapMove({ x: -50, y: -9, w: 200, h: 150 }, [])).toMatchObject({ x: 0, y: 0 });
  });
});

describe("resizing a window", () => {
  const start = { x: 100, y: 100, w: 300, h: 200 };

  it("moves only the edges of the handle, and they snap", () => {
    expect(snapResize(start, 53, 0, { right: true }, [])).toEqual({ x: 100, y: 100, w: 356, h: 200 }); // right edge 453 → 456 on the grid
    expect(snapResize(start, 0, 0, { right: true }, [{ x: 405, y: 0, w: 10, h: 10 }])).toEqual({ x: 100, y: 100, w: 301, h: 200 }); // just before 405
    expect(snapResize(start, -21, -17, { left: true, top: true }, [], true)).toEqual({ x: 79, y: 83, w: 321, h: 217 });
  });

  it("keeps the opposite edge still and never gets smaller than the minimum", () => {
    const r = snapResize(start, 500, 500, { left: true, top: true }, []);
    expect(r).toEqual({ x: 400 - MIN_W, y: 300 - MIN_H, w: MIN_W, h: MIN_H });
    expect(snapResize(start, -500, -500, { right: true, bottom: true }, [])).toMatchObject({ x: 100, y: 100, w: MIN_W, h: MIN_H });
  });
});

describe("placing a new window", () => {
  it("goes beside the one it came from, right or below", () => {
    const from = W("a", 8, 8, 300, 400);
    expect(placeNew([from], { w: 400, h: 300 }, view, from, "right")).toEqual({ x: 312, y: 8, w: 400, h: 300 });
    expect(placeNew([from], { w: 400, h: 300 }, view, from, "below")).toEqual({ x: 8, y: 412, w: 400, h: 300 });
  });

  it("tries the other side, then any empty spot on screen", () => {
    const from = W("a", 8, 8, 300, 400);
    const right = W("b", 312, 8, 400, 400);
    expect(placeNew([from, right], { w: 400, h: 300 }, view, from, "right")).toEqual({ x: 8, y: 412, w: 400, h: 300 });
    const placed = placeNew([W("big", 0, 0, 1200, 400)], { w: 400, h: 300 }, view);
    expect(placed.y).toBeGreaterThanOrEqual(400);
    expect(placed.y + placed.h).toBeLessThanOrEqual(800);
  });

  it("cascades from the screen's corner when the screen is full, never exactly under another", () => {
    const full = W("full", 0, 0, 1200, 800);
    const first = placeNew([full], { w: 400, h: 300 }, view);
    const second = placeNew([full, W("n", first.x, first.y, 400, 300)], { w: 400, h: 300 }, view);
    expect(first).toEqual({ x: 24, y: 24, w: 400, h: 300 });
    expect(second).toEqual({ x: 48, y: 48, w: 400, h: 300 });
  });

  it("uses the part of the canvas that is on screen", () => {
    expect(placeNew([], { w: 400, h: 300 }, { x: 2000, y: 1000, w: 1200, h: 800 })).toEqual({ x: 2008, y: 1008, w: 400, h: 300 });
  });
});

describe("the canvas", () => {
  it("is the screen, or bigger when windows sit past it (with room to drag into)", () => {
    expect(extent([], view)).toEqual({ w: 1200, h: 800 });
    expect(extent([W("a", 3000, 1000, 500, 200)], view)).toEqual({ w: 3500 + ROOM, h: 1200 + ROOM });
  });

  it("brings a window to the front and keeps the order of the others", () => {
    const ws = [W("a", 0, 0, 200, 200, 3), W("b", 0, 0, 200, 200, 9), W("c", 0, 0, 200, 200, 5)];
    const out = toFront(ws, "a");
    expect(out.map((w) => [w.id, w.z])).toEqual([["a", 3], ["b", 2], ["c", 1]]);
    expect(toFront(out, "a")).toBe(out); // already in front: nothing to save
  });

  it("tidies boxes to whole pixels within limits", () => {
    expect(tidy({ x: 1.6, y: -3, w: 20, h: 99999 })).toEqual({ x: 2, y: 0, w: MIN_W, h: 8000 });
  });

  it("accepts only well-formed windows", () => {
    expect(isCanvas({ windows: [W("a", 0, 0, 200, 200)] })).toBe(true);
    expect(isCanvas({ windows: [W("a", 0, 0, 200, 200), W("a", 9, 9, 200, 200)] })).toBe(false); // one id, two windows
    expect(isCanvas({ windows: [{ ...W("a", 0, 0, 200, 200), x: Number.NaN }] })).toBe(false);
    expect(isCanvas({ windows: [{ ...W("a", 0, 0, 200, 200), params: [] }] })).toBe(false);
    expect(isCanvas({ windows: [{ ...W("a", 0, 0, 200, 200), id: "" }] })).toBe(false);
    expect(isCanvas({ windows: Array.from({ length: MAX_WINDOWS + 1 }, (_, i) => W(`w${i}`, 0, 0, 200, 200)) })).toBe(false);
    expect(isCanvas({})).toBe(false);
  });
});
