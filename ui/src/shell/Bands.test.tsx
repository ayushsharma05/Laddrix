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
