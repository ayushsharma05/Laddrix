import { act, fireEvent, render, screen } from "@testing-library/react";
import { AppProvider, createApp } from "../app/context";
import { fakeRest, fakeStream } from "../test/fakes";
import { ME } from "../test/fixtures";
import { EnvStrip } from "./EnvStrip";

const KITE = { id: "acc-k", broker: "kite", broker_client_id: "AB1234", can_trade: true, running: true, stopped: false, stop_reason: "" };

function setup(session: "live" | "login_needed" | null, routes: Record<string, unknown> = {}) {
  const server = fakeRest(routes);
  const { stream } = fakeStream();
  const app = createApp({ rest: server.rest, stream });
  app.store.setMe({ ...ME, accounts: [...ME.accounts, { ...KITE, broker_session: session }] });
  app.store.selectAccount("acc-k");
  render(
    <AppProvider app={app}>
      <EnvStrip />
    </AppProvider>,
  );
  return { ...server, app };
}

describe("EnvStrip", () => {
  it("a live account says orders go to the exchange", () => {
    setup("live");
    expect(screen.getByText(/LIVE · KITE · AB1234 · orders go to the exchange/)).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("without today's login it says orders are refused and offers the broker's login", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    const { calls } = setup("login_needed", { "GET /api/accounts/acc-k/broker-login": { url: "https://kite.example/connect/login?state=s1" } });
    expect(screen.getByRole("status").textContent).toContain("not logged in to Kite today: orders are refused");
    fireEvent.click(screen.getByRole("button", { name: "Log in to Kite" }));
    await vi.waitFor(() => expect(assign).toHaveBeenCalledWith("https://kite.example/connect/login?state=s1"));
    expect(calls.map((c) => c.path)).toEqual(["/api/accounts/acc-k/broker-login"]);
    vi.unstubAllGlobals();
  });

  it("a refused login link is reported, and the button works again", async () => {
    const { app } = setup("login_needed", { "GET /api/accounts/acc-k/broker-login": () => ({ status: 403, body: { detail: "only the account holder can log in to the broker" } }) });
    const button = screen.getByRole("button", { name: "Log in to Kite" }) as HTMLButtonElement;
    fireEvent.click(button);
    await vi.waitFor(() => expect(app.store.getState().messages.some((m) => m.text.includes("only the account holder"))).toBe(true));
    await act(async () => {});
    expect(button.disabled).toBe(false);
  });
});
