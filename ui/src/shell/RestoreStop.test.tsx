import { act, fireEvent, render, screen } from "@testing-library/react";
import { AppProvider, createApp } from "../app/context";
import { fakeRest, fakeStream } from "../test/fakes";
import { ME } from "../test/fixtures";
import { Bands } from "./Bands";

const FUT = "NFO:NIFTY26OCTFUT";
const BASE = "/api/accounts/acc-a/protection";
const band = { type: "ops", key: `unprotected:acc-a:${FUT}`, level: "ERROR", text: `UNPROTECTED acc-a: 75 ${FUT} (long) is open with no stop`, active: true, at: "2026-10-16T04:00:00+00:00" };

function setup(routes: Record<string, unknown>) {
  const server = fakeRest(routes);
  const { stream, socket } = fakeStream();
  const app = createApp({ rest: server.rest, stream });
  app.store.setMe(ME);
  stream.start();
  socket().open();
  render(
    <AppProvider app={app}>
      <Bands />
    </AppProvider>,
  );
  act(() => socket().receive(band));
  return { ...server, app };
}

describe("Restore stop in the UNPROTECTED band", () => {
  it("restores after one confirmation, saying what it starts", async () => {
    const plan = "TRAILING_STOP SELL 75 NFO:NIFTY26OCTFUT NRML {'trail_paise': 2000}";
    const { calls, app } = setup({ [`GET ${BASE}`]: { unprotected: [{ instrument_id: FUT, restore_as: plan, blocked_by: null }] }, [`POST ${BASE}/restore`]: {} });
    fireEvent.click(await screen.findByRole("button", { name: "Restore stop" }));
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0); // the first click only asks
    fireEvent.click(screen.getByRole("button", { name: `Confirm: ${plan}` }));
    await vi.waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ instrument_id: FUT, confirm: true }));
    await vi.waitFor(() => expect(app.store.getState().messages[0]!.text).toBe(`Stop restored: ${plan}`));
  });

  it("says why it can't restore now, with no button", async () => {
    setup({ [`GET ${BASE}`]: { unprotected: [{ instrument_id: FUT, restore_as: null, blocked_by: "MT4 of the old stop is still working: wait until its cancel settles" }] } });
    expect(await screen.findByText("Restore stop: not now (MT4 of the old stop is still working: wait until its cancel settles)")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Restore/ })).toBeNull();
  });
});
