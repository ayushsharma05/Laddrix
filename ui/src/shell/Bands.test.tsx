import { act, render, screen } from "@testing-library/react";
import { AppProvider, createApp } from "../app/context";
import { fakeRest, fakeStream } from "../test/fakes";
import { ME } from "../test/fixtures";
import { Bands } from "./Bands";

function setup() {
  const { rest } = fakeRest();
  const { stream, socket } = fakeStream();
  const app = createApp({ rest, stream });
  app.store.setMe(ME);
  stream.start();
  socket().open();
  render(
    <AppProvider app={app}>
      <Bands />
    </AppProvider>,
  );
  return { app, socket };
}

const ops = (key: string, level: string, text: string, active = true) => ({ type: "ops", key, level, text, active, at: "2026-10-14T03:30:00+00:00" });

describe("operational alerts", () => {
  it("show as bands, errors first, until the server clears them; each also goes to Messages", () => {
    const { app, socket } = setup();
    act(() => socket().receive(ops("disk", "WARN", "Data disk low: 900 MB free")));
    act(() => socket().receive(ops("broker_login:acc-a", "ERROR", "Log in to Kite for acc-a (AB1): the market opens in 9 min")));
    const bands = screen.getAllByText(/ACTION NEEDED|HEADS UP/).map((b) => b.parentElement!.textContent);
    expect(bands).toEqual(["ACTION NEEDED" + "Log in to Kite for acc-a (AB1): the market opens in 9 min", "HEADS UP" + "Data disk low: 900 MB free"]);
    act(() => socket().receive(ops("broker_login:acc-a", "OK", "acc-a: logged in to kite for today", false)));
    expect(screen.queryByText("ACTION NEEDED")).toBeNull();
    const texts = app.store.getState().messages.map((m) => `${m.level} ${m.text}`);
    expect(texts).toContain("OK acc-a: logged in to kite for today");
    expect(texts).toContain("ERROR Log in to Kite for acc-a (AB1): the market opens in 9 min");
  });
});

describe("loading after a (re)connect (#268)", () => {
  it("says while the account's orders and positions are loading, and why if they failed", () => {
    const { app } = setup();
    const accountId = app.store.getState().accountId!;
    act(() => app.store.startSync(1, [accountId]));
    expect(screen.getByText(`LOADING ${accountId}`).parentElement!.textContent).toContain("new orders wait; cancels still go");
    act(() => app.store.syncFailed(1, "server busy"));
    expect(screen.getByText(`COULDN'T LOAD ${accountId}`).parentElement!.textContent).toContain("server busy · trying again");
    act(() => app.store.accountSynced(1, accountId));
    expect(screen.queryByText(/LOADING|COULDN'T LOAD/)).toBeNull();
  });

  it("an older load's answer changes nothing", () => {
    const { app } = setup();
    const accountId = app.store.getState().accountId!;
    act(() => app.store.startSync(2, [accountId]));
    act(() => app.store.accountSynced(1, accountId)); // from load 1: too late
    expect(screen.getByText(`LOADING ${accountId}`)).toBeTruthy();
  });

  it("protections that couldn't be loaded are said, not shown as none", () => {
    const { app } = setup();
    const accountId = app.store.getState().accountId!;
    act(() => {
      app.store.startSync(1, [accountId]);
      app.store.accountSynced(1, accountId, ["algos"]);
    });
    const band = screen.getByText("ALGOS UNAVAILABLE").parentElement!;
    expect(band.getAttribute("role")).toBe("status");
    expect(band.textContent).toContain("may be running but aren't shown");
  });
});
