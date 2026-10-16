import { act, fireEvent, render, screen, within } from "@testing-library/react";
import type { MarketAlert } from "../../api/types";
import { AppProvider, createApp } from "../../app/context";
import { fakeRest, fakeStream } from "../../test/fakes";
import { ME } from "../../test/fixtures";
import { AlertsManager } from "./AlertsManager";
import { FUT, SPEC, alert } from "./testdata";

function setup(routes: Record<string, unknown> = {}, params: Record<string, unknown> = {}) {
  const server = fakeRest({
    "GET /api/alerts/spec": SPEC,
    "GET /api/alerts": [alert("A1"), alert("A2", { kind: "PCT_MOVE", params: { pct: 2, direction: "up" }, done: true, active: false, fired_count: 1, status: "fired", last_fired_at: "2026-10-16T06:12:00+00:00", webhook: true, webhook_ok_at: "2026-10-16T06:12:01+00:00" })],
    "GET /api/alerts/webhook": { url: null, set: false },
    "POST /api/alerts": (body: unknown) => ({ status: 201, body: alert("A9", body as Partial<MarketAlert>) }),
    "PATCH /api/alerts/A1": (body: unknown) => ({ status: 200, body: alert("A1", { ...(body as object), status: "off", active: false }) }),
    "DELETE /api/alerts/A1": () => ({ status: 204 }),
    ...routes,
  });
  const { stream, socket } = fakeStream();
  const app = createApp({ rest: server.rest, stream });
  app.store.setMe(ME);
  stream.start();
  socket().open();
  const props = { params: { link: "pink" as const, ...params }, setParams: vi.fn(), setInstrument: vi.fn(), publish: vi.fn(), open: vi.fn() };
  render(
    <AppProvider app={app}>
      <AlertsManager {...props} />
    </AppProvider>,
  );
  return { ...server, ...props, app, socket };
}

const rows = async () => within(await screen.findByRole("grid", { name: "Alerts" })).getAllByRole("row");

describe("Alerts manager", () => {
  it("lists the user's alerts in words, with their state, firings and webhook result", async () => {
    setup();
    const [a1, a2] = await rows();
    expect(a1!.textContent).toBe("NIFTY26OCTFUTPrice crosses above 25,100.00watching0×–OffDelete…");
    expect(a2!.textContent).toContain("Moves from the previous close 2% · up");
    expect(a2!.textContent).toContain("fired1×11:42:00sent ✓On");
    expect(screen.getByRole("button", { name: "Alerts (1)" })).toBeTruthy(); // one still active
  });

  it("follows the server live: a fired alert updates its row and lands in Messages", async () => {
    const { socket, app } = setup();
    await rows();
    act(() => socket().receive({ type: "market_alert_state", alert: alert("A1", { status: "already true: fires the next time it crosses" }) }));
    expect((await rows())[0]!.textContent).toContain("already true");
    act(() => socket().receive({ type: "market_alert", alert: alert("A1", { done: true, active: false, fired_count: 1, status: "fired" }), text: "NIFTY26OCTFUT crossed above 25,100.00 (last 25,101.00)", at: "2026-10-16T06:20:00+00:00" }));
    expect((await rows())[0]!.textContent).toContain("fired1×");
    expect(app.store.getState().messages[0]!.text).toBe("Alert: NIFTY26OCTFUT crossed above 25,100.00 (last 25,101.00)");
  });

  it("switches an alert off and deletes one only after a second click", async () => {
    const { calls } = setup();
    const [a1] = await rows();
    fireEvent.click(within(a1!).getByRole("button", { name: "Off" }));
    await vi.waitFor(() => expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ enabled: false }));
    await vi.waitFor(async () => expect((await rows())[0]!.textContent).toContain("off"));
    fireEvent.click(within((await rows())[0]!).getByRole("button", { name: "Delete…" }));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    fireEvent.click(within((await rows())[0]!).getByRole("button", { name: "Confirm delete" }));
    await vi.waitFor(async () => expect(await rows()).toHaveLength(1));
  });

  it("the New form is drawn from the server's spec and makes an alert", async () => {
    const { calls, setParams } = setup({}, { mode: "new", instrumentId: FUT });
    const form = await screen.findByRole("form", { name: "New alert" });
    expect(within(within(form).getByLabelText("Alert kind")).getAllByRole("option").map((o) => o.textContent)).toEqual(["Price crosses above", "Moves from the previous close", "Spread crosses above"]);
    fireEvent.change(within(form).getByLabelText("Alert kind"), { target: { value: "PCT_MOVE" } });
    fireEvent.change(within(form).getByRole("textbox", { name: "Move" }), { target: { value: "1.5" } });
    fireEvent.change(within(form).getByRole("combobox", { name: "Direction" }), { target: { value: "down" } });
    fireEvent.change(within(form).getByLabelText("Repeat"), { target: { value: "every" } });
    fireEvent.change(within(form).getByLabelText("Cool-down"), { target: { value: "60" } });
    fireEvent.change(within(form).getByLabelText("Note"), { target: { value: "dip" } });
    expect((within(form).getByLabelText("Send to my webhook") as HTMLInputElement).disabled).toBe(true); // none set
    fireEvent.click(within(form).getByRole("button", { name: "Make alert" }));
    await vi.waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ kind: "PCT_MOVE", instrument_id: FUT, params: { pct: 1.5, direction: "down" }, repeat: "every", cooldown_s: 60, note: "dip", webhook: false }));
    await vi.waitFor(() => expect(setParams).toHaveBeenCalledWith({ mode: "list" }));
  });

  it("problems in the form are listed and nothing is sent; a refusal is shown", async () => {
    const { calls } = setup({ "POST /api/alerts": () => ({ status: 422, body: { detail: "unknown instrument NFO:X" } }) }, { mode: "new" });
    const form = await screen.findByRole("form", { name: "New alert" });
    fireEvent.click(within(form).getByRole("button", { name: "Make alert" }));
    expect((await within(form).findByRole("alert")).textContent).toBe("Pick a contractPrice is required");
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("the webhook: saved, its secret shown once, tested, removed", async () => {
    const { calls } = setup(
      {
        "PUT /api/alerts/webhook": (body: unknown) => ({ status: 200, body: { url: (body as { url: string }).url, set: true, secret: "s3cret-once" } }),
        "POST /api/alerts/webhook/test": () => ({ status: 200, body: { ok: false, error: "refused: answered 401" } }),
        "DELETE /api/alerts/webhook": () => ({ status: 204 }),
      },
      { mode: "webhook" },
    );
    const form = await screen.findByRole("form", { name: "Webhook" });
    fireEvent.change(within(form).getByLabelText("Webhook address"), { target: { value: "https://hooks.example.com/x" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));
    expect((await within(form).findByRole("status")).textContent).toContain("s3cret-once");
    expect(calls.find((c) => c.method === "PUT")?.body).toEqual({ url: "https://hooks.example.com/x" });
    fireEvent.click(within(form).getByRole("button", { name: "Test" }));
    await vi.waitFor(() => expect(within(form).getByRole("alert").textContent).toBe("Test failed: refused: answered 401"));
    fireEvent.click(within(form).getByRole("button", { name: "Remove" }));
    await vi.waitFor(() => expect(within(form).queryByRole("status")).toBeNull()); // the secret is gone from the screen
    expect(within(form).getByRole("button", { name: "Save" })).toBeTruthy();
  });
});
