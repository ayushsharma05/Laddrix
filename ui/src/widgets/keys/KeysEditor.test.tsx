import { fireEvent, render, screen, within } from "@testing-library/react";
import { AppProvider, createApp } from "../../app/context";
import { KeyBindings } from "../../keys/bindings";
import { fakeRest, fakeStream } from "../../test/fakes";
import { ME } from "../../test/fixtures";
import { KeysEditor } from "./KeysEditor";

const PATH = "/api/me/settings/keys";

function setup(saved: unknown = null, put: () => { status: number; body?: unknown } = () => ({ status: 200, body: {} })) {
  const server = fakeRest({ [`GET ${PATH}`]: { name: "keys", value: saved }, [`PUT ${PATH}`]: put });
  const app = createApp({ rest: server.rest, stream: fakeStream().stream });
  app.store.setMe(ME);
  const props = { params: { link: "none" as const }, setParams: vi.fn(), setInstrument: vi.fn(), publish: vi.fn(), open: vi.fn() };
  render(
    <AppProvider app={app}>
      <KeysEditor {...props} />
    </AppProvider>,
  );
  return { ...server, app };
}

const row = (label: string) => screen.getByRole("group", { name: label });
const press = (el: Element, key: string, mods: Partial<KeyboardEvent> = {}) => fireEvent.keyDown(el, { key, code: /^[a-z]$/i.test(key) ? `Key${key.toUpperCase()}` : "", ...mods });

describe("Keyboard shortcuts editor", () => {
  it("lists every action by where it works, with its keys; STOP's key is fixed", () => {
    setup();
    expect(screen.getAllByRole("region").map((r) => r.getAttribute("aria-label"))).toEqual(["Anywhere", "Order ticket (when it has the focus)", "Ladder (when it has the focus)", "Chart (when it has the focus)"]);
    const stop = row("STOP trading (opens the stop dialog)");
    expect(within(stop).getByText(/Ctrl\+Shift\+K/).getAttribute("title")).toBe("Always works: it can't be removed");
    expect(within(stop).queryByRole("button", { name: /^Remove/ })).toBeNull();
    expect(within(row("Send the order (its confirm rules still apply)")).getByText("Enter")).toBeTruthy();
  });

  it("a pressed key is checked, then saved on the server for this user", async () => {
    const { calls, app } = setup();
    const chart = row("Open a chart");
    fireEvent.click(within(chart).getByRole("button", { name: "Add a key to Open a chart" }));
    const capture = within(chart).getByRole("button", { name: "Press a key…" });
    press(capture, "w", { ctrlKey: true });
    expect(screen.getByRole("alert").textContent).toBe("Ctrl+W belongs to the browser (closing, reloading or moving between tabs and fields)");
    press(capture, "s", { ctrlKey: true });
    expect(screen.getByRole("alert").textContent).toBe("Ctrl+S is already “Save the layout”");
    press(capture, "Control", { ctrlKey: true }); // a modifier alone: still waiting
    expect(within(chart).getByRole("button", { name: "Press a key…" })).toBeTruthy();
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(0);
    press(capture, "F2");
    await vi.waitFor(() => expect(calls.filter((c) => c.method === "PUT")).toHaveLength(1));
    expect(calls.find((c) => c.method === "PUT")!.body).toEqual({ value: { bindings: { "open-chart": ["Alt+C", "F2"] } } });
    expect(app.keys.get()).toEqual({ "open-chart": ["Alt+C", "F2"] });
    expect(within(chart).getByText("F2")).toBeTruthy();
    fireEvent.click(within(chart).getByRole("button", { name: "Remove Alt+C from Open a chart" }));
    await vi.waitFor(() => expect(app.keys.get()).toEqual({ "open-chart": ["F2"] }));
    fireEvent.click(within(chart).getByRole("button", { name: "Default" }));
    await vi.waitFor(() => expect(app.keys.get()).toEqual({}));
    expect(calls.filter((c) => c.method === "PUT").at(-1)!.body).toEqual({ value: null }); // nothing left: defaults
  });

  it("Escape cancels choosing; a refused save keeps the old keys and says why", async () => {
    const { app } = setup(null, () => ({ status: 422, body: { detail: "the keys setting is larger than 16 KB" } }));
    const chart = row("Open a chart");
    fireEvent.click(within(chart).getByRole("button", { name: "Add a key to Open a chart" }));
    press(within(chart).getByRole("button", { name: "Press a key…" }), "Escape");
    expect(within(chart).queryByRole("button", { name: "Press a key…" })).toBeNull();
    fireEvent.click(within(chart).getByRole("button", { name: "Add a key to Open a chart" }));
    press(within(chart).getByRole("button", { name: "Press a key…" }), "F2");
    expect((await screen.findByRole("alert")).textContent).toBe("Not saved: the keys setting is larger than 16 KB");
    expect(app.keys.get()).toEqual({});
  });

  it("all back to the defaults takes a second click", async () => {
    const { app, calls } = setup();
    await app.keys.save({ "open-chart": ["F2"] });
    fireEvent.click(await screen.findByRole("button", { name: "All back to the defaults…" }));
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(1); // only the save above
    fireEvent.click(screen.getByRole("button", { name: "Confirm: back to the defaults" }));
    await vi.waitFor(() => expect(app.keys.get()).toEqual({}));
  });
});

describe("the user's keys from the server", () => {
  it("loads saved choices, keeping only valid ones; a failure leaves the defaults", async () => {
    const ok = fakeRest({ [`GET ${PATH}`]: { name: "keys", value: { bindings: { "open-chart": ["F2"], gone: ["F3"], "open-ladder": ["Ctrl+W"] } } } });
    const keys = new KeyBindings(ok.rest);
    const seen: number[] = [];
    keys.subscribe(() => seen.push(1));
    await keys.load();
    expect(keys.get()).toEqual({ "open-chart": ["F2"] }); // a browser key only: the default instead
    expect(seen).toHaveLength(1);
    const down = new KeyBindings(fakeRest({}).rest); // 404: nothing there
    await down.load();
    expect(down.get()).toEqual({});
  });
});
