import { act, fireEvent, render, screen } from "@testing-library/react";
import type { DockviewApi } from "dockview-react";
import { useSyncExternalStore } from "react";
import { AppProvider, createApp } from "../app/context";
import { fakeRest, fakeStream } from "../test/fakes";
import type { CanvasWindow } from "./canvas";
import { WorkspaceController } from "./controller";
import { FreeCanvas } from "./FreeCanvas";
import { LayoutStore } from "./layouts";
import { ControllerContext } from "./Panel";
import { registerWidget, type WidgetProps } from "./registry";

// jsdom has no PointerEvent: a MouseEvent carries the same position and buttons
if (typeof window.PointerEvent === "undefined") Object.assign(window, { PointerEvent: class PointerEvent extends MouseEvent {} });

type ProbeParams = { link: "none" | "pink"; n: number };

function Probe({ params, setParams }: WidgetProps<ProbeParams>) {
  return (
    <button type="button" onClick={() => setParams({ n: params.n + 1 })}>
      count {params.n}
    </button>
  );
}

registerWidget<ProbeParams>({
  kind: "frame-probe",
  title: "Probe",
  description: "test widget",
  component: Probe,
  defaults: () => ({ link: "pink", n: 1 }),
  link: "follows",
  tabTitle: (p) => `Probe ${p.n}`,
});

const start = (id: string, x: number, y: number, z: number): CanvasWindow => ({
  id,
  kind: "frame-probe",
  params: { link: "pink", n: 1 },
  x,
  y,
  w: 400,
  h: 300,
  z,
});

/** Just enough of dockview: it stays empty behind a free page. */
const dock = { clear() {}, toJSON: () => ({}), onDidLayoutChange: () => ({ dispose() {} }) } as unknown as DockviewApi;

function Harness({ controller }: { controller: WorkspaceController }) {
  useSyncExternalStore(controller.subscribe, controller.getVersion);
  return <FreeCanvas controller={controller} empty={<p>This page is empty.</p>} />;
}

async function setup(windows = [start("a", 8, 8, 1), { ...start("b", 600, 1400, 2), params: { link: "pink" as const, n: 2 } }]) {
  const app = createApp({ rest: fakeRest({}).rest, stream: fakeStream().stream });
  const controller = new WorkspaceController({
    layouts: new LayoutStore(fakeRest({}).rest, null, "ayush"),
    buildDefault: () => {},
    buildDefaultCanvas: (page) => (page === "Main" ? windows : []), // as in the app: other pages start empty
    notify: () => {},
    setTimer: () => 0 as unknown as ReturnType<typeof setTimeout>,
    clearTimer: () => {},
  });
  await controller.start(dock);
  render(
    <AppProvider app={app}>
      <ControllerContext.Provider value={controller}>
        <Harness controller={controller} />
      </ControllerContext.Provider>
    </AppProvider>,
  );
  const frame = (name: string) => screen.getByRole("region", { name }) as HTMLElement;
  const box = (id: string) => controller.canvas!.windows.find((w) => w.id === id)!;
  return { controller, frame, box };
}

describe("a free page", () => {
  it("draws each window at its own place and size, in its z order, past the screen too", async () => {
    const { frame } = await setup([start("a", 8, 8, 1), { ...start("b", 600, 1400, 2), params: { link: "pink" as const, n: 2 } }]);
    const a = frame("Probe 1");
    expect([a.style.left, a.style.top, a.style.width, a.style.height, a.style.zIndex]).toEqual(["8px", "8px", "400px", "300px", "1"]);
    expect(frame("Probe 2").style.top).toBe("1400px");
    const surface = screen.getByTestId("free-canvas").firstElementChild as HTMLElement;
    expect(surface.style.height).toBe(`${1400 + 300 + 240}px`); // the page scrolls down to it
    expect(a.querySelectorAll(".rz")).toHaveLength(8); // every edge and corner resizes
  });

  it("moves with the arrow keys on its title bar (Alt: a pixel), and resizes with Shift", async () => {
    const { frame, box } = await setup();
    const title = frame("Probe 1").querySelector(".frame-title")!;
    fireEvent.keyDown(title, { key: "ArrowRight" });
    fireEvent.keyDown(title, { key: "ArrowDown" });
    fireEvent.keyDown(title, { key: "ArrowLeft", altKey: true });
    fireEvent.keyDown(title, { key: "ArrowRight", shiftKey: true });
    expect(box("a")).toMatchObject({ x: 15, y: 16, w: 408, h: 300 });
  });

  it("is dragged by its title bar and snaps beside a neighbour, unless Alt is held", async () => {
    const { frame, box } = await setup([start("a", 8, 8, 1), { ...start("b", 1000, 8, 2), params: { link: "pink" as const, n: 2 } }]);
    const title = frame("Probe 1").querySelector(".frame-title")!;
    fireEvent.pointerDown(title, { button: 0, clientX: 100, clientY: 20 });
    fireEvent.pointerMove(window, { clientX: 685, clientY: 22 }); // x 593, so its right edge is 993: 3 px from b's gap
    fireEvent.pointerUp(window);
    expect(box("a")).toMatchObject({ x: 596, y: 8 }); // right edge 996: 4 px before b
    fireEvent.pointerDown(title, { button: 0, clientX: 100, clientY: 20 });
    fireEvent.pointerMove(window, { clientX: 103, clientY: 25, altKey: true });
    fireEvent.pointerUp(window);
    expect(box("a")).toMatchObject({ x: 599, y: 13 });
  });

  it("resizes from an edge", async () => {
    const { frame, box } = await setup();
    const corner = frame("Probe 1").querySelector(".rz-se")!;
    fireEvent.pointerDown(corner, { button: 0, clientX: 408, clientY: 308 });
    fireEvent.pointerMove(window, { clientX: 702, clientY: 455 });
    fireEvent.pointerUp(window);
    expect(box("a")).toMatchObject({ x: 8, y: 8, w: 696, h: 448 }); // right edge 702 → 704, bottom 455 → 456 on the grid
  });

  it("comes to the front when clicked", async () => {
    const { frame, box } = await setup();
    fireEvent.pointerDown(frame("Probe 1"));
    expect(box("a").z).toBeGreaterThan(box("b").z);
  });

  it("saves the widget's settings and its link group with the window", async () => {
    const { box } = await setup([start("a", 8, 8, 1)]);
    fireEvent.click(screen.getByText("count 1"));
    expect(box("a").params).toMatchObject({ n: 2 });
    expect(screen.getByText("Probe 2")).toBeTruthy(); // the title follows
    fireEvent.click(screen.getByRole("button", { name: "Link group pink" }));
    expect(box("a").params.link).not.toBe("pink");
  });

  it("fills the visible page on a double-click, and goes back", async () => {
    const { frame } = await setup();
    const title = frame("Probe 1").querySelector(".frame-title")!;
    fireEvent.doubleClick(title);
    expect(frame("Probe 1").className).toContain("maximized");
    fireEvent.doubleClick(title);
    expect(frame("Probe 1").className).not.toContain("maximized");
  });

  it("a window that left the page doesn't come back maximized (another page, then back)", async () => {
    const { frame, controller } = await setup();
    fireEvent.doubleClick(frame("Probe 1").querySelector(".frame-title")!);
    expect(frame("Probe 1").className).toContain("maximized");
    await act(() => controller.addPage("Second")); // its windows go away with the page
    await act(() => controller.switchTo(0)); // and come back, the same windows
    expect(frame("Probe 1").className).not.toContain("maximized");
  });

  it("closes, and can't be moved, resized or closed while locked", async () => {
    const { controller, frame, box } = await setup();
    act(() => controller.setLocked(true));
    const a = frame("Probe 1");
    expect(a.querySelectorAll(".rz")).toHaveLength(0);
    expect((a.querySelector('[aria-label="Close"]') as HTMLButtonElement).disabled).toBe(true);
    const handled = !fireEvent.keyDown(a.querySelector(".frame-title")!, { key: "ArrowRight" });
    expect(box("a").x).toBe(8);
    expect(handled).toBe(false); // the arrow key is left to the page (it scrolls)
    act(() => controller.setLocked(false));
    fireEvent.click(frame("Probe 1").querySelector('[aria-label="Close"]')!);
    expect(screen.queryByRole("region", { name: "Probe 1" })).toBeNull();
  });

  it("says the page is empty when it has no windows", async () => {
    await setup([]);
    expect(screen.getByText("This page is empty.")).toBeTruthy();
  });
});
