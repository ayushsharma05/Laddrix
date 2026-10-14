// Where the main window was, so it reopens there, and never off-screen: if the monitor it
// was on is gone (laptop undocked), it comes back centred on the main screen.

export interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface WindowState extends Box {
  maximized: boolean;
}

export const DEFAULT_SIZE = { width: 1600, height: 950 };
const MIN = { width: 800, height: 500 };

/** How much of `a` overlaps `b`, in square pixels. */
function overlap(a: Box, b: Box): number {
  const w = Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x);
  const h = Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y);
  return w > 0 && h > 0 ? w * h : 0;
}

/**
 * The saved place if enough of it (a quarter) is on a screen that still exists; otherwise a
 * default-sized window centred on the primary screen. Sizes never below the minimum or above
 * the screen they're on.
 */
export function placeWindow(saved: Partial<WindowState> | null, screens: Box[], primary: Box): WindowState {
  const valid =
    saved &&
    [saved.x, saved.y, saved.width, saved.height].every((n) => typeof n === "number" && Number.isFinite(n)) &&
    saved.width! >= MIN.width &&
    saved.height! >= MIN.height;
  if (valid) {
    const box = saved as Box;
    const home = screens.reduce<Box | null>((best, s) => (overlap(box, s) > (best ? overlap(box, best) : 0) ? s : best), null);
    if (home && overlap(box, home) >= (box.width * box.height) / 4) {
      return {
        x: box.x,
        y: box.y,
        width: Math.min(box.width, home.width),
        height: Math.min(box.height, home.height),
        maximized: saved.maximized === true,
      };
    }
  }
  const width = Math.min(DEFAULT_SIZE.width, primary.width);
  const height = Math.min(DEFAULT_SIZE.height, primary.height);
  return {
    x: primary.x + Math.round((primary.width - width) / 2),
    y: primary.y + Math.round((primary.height - height) / 2),
    width,
    height,
    maximized: false,
  };
}
