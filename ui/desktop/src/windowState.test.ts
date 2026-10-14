import { DEFAULT_SIZE, placeWindow } from "./windowState";

const LAPTOP = { x: 0, y: 0, width: 1920, height: 1040 };
const MONITOR = { x: 1920, y: 0, width: 2560, height: 1400 };

describe("where the main window opens", () => {
  it("where it was, on a screen that still exists", () => {
    const saved = { x: 2000, y: 50, width: 2400, height: 1300, maximized: true };
    expect(placeWindow(saved, [LAPTOP, MONITOR], LAPTOP)).toEqual(saved);
  });

  it("centred on the main screen when its monitor is gone (laptop undocked)", () => {
    const saved = { x: 2000, y: 50, width: 2400, height: 1300, maximized: false };
    expect(placeWindow(saved, [LAPTOP], LAPTOP)).toEqual({ x: 160, y: 45, ...DEFAULT_SIZE, maximized: false });
  });

  it("never bigger than its screen; mostly off-screen counts as gone", () => {
    expect(placeWindow({ x: 0, y: 0, width: 3000, height: 2000 }, [LAPTOP], LAPTOP)).toMatchObject({ width: 1920, height: 1040 });
    expect(placeWindow({ x: 1700, y: 900, width: 1600, height: 950 }, [LAPTOP], LAPTOP).x).toBe(160);
  });

  it("ignores damaged or tiny saved places", () => {
    expect(placeWindow(null, [LAPTOP], LAPTOP)).toMatchObject({ x: 160, y: 45 });
    expect(placeWindow({ x: Number.NaN, y: 0, width: 1600, height: 950 }, [LAPTOP], LAPTOP).x).toBe(160);
    expect(placeWindow({ x: 0, y: 0, width: 100, height: 100 }, [LAPTOP], LAPTOP).width).toBe(1600);
    const small = { x: 0, y: 0, width: 1280, height: 720 };
    expect(placeWindow(null, [small], small)).toMatchObject({ width: 1280, height: 720, x: 0, y: 0 });
  });
});
