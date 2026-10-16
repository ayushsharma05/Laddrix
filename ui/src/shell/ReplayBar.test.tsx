import { fireEvent, render, screen } from "@testing-library/react";
import { AppProvider, createApp } from "../app/context";
import { fakeRest, fakeStream } from "../test/fakes";
import { ME } from "../test/fixtures";
import { ReplayBar, type ReplayStatus } from "./ReplayBar";

const STATUS: ReplayStatus = { day: "2026-10-16", state: "playing", speed: 10, at: "2026-10-16T05:12:15+00:00", fed: 120, seeking_to: null };

function setup(routes: Record<string, unknown>) {
  const server = fakeRest(routes);
  const { stream } = fakeStream();
  const app = createApp({ rest: server.rest, stream });
  app.store.setMe(ME);
  render(
    <AppProvider app={app}>
      <ReplayBar />
    </AppProvider>,
  );
  return server;
}

describe("replay bar", () => {
  it("shows the replayed day and time, and its controls ask the server", async () => {
    const { calls } = setup({
      "GET /api/replay": STATUS,
      "POST /api/replay": (body: unknown) => ({ status: 200, body: { ...STATUS, ...(body as { action: string }).action === "pause" ? { state: "paused" } : { speed: 60 } } }),
    });
    const bar = await screen.findByRole("region", { name: "Replay" });
    expect(bar.textContent).toContain("REPLAY 2026-10-16");
    expect(bar.textContent).toContain("10:42:15"); // India time
    expect(screen.getByRole("button", { name: "10×" }).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "60×" }));
    await vi.waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ action: "speed", speed: 60 }));
    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    expect(await screen.findByRole("button", { name: "Resume" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "+15 min" }));
    await vi.waitFor(() => expect(calls.at(-1)!.body).toEqual({ action: "seek", at: "2026-10-16T05:27:15.000Z" }));
  });

  it("isn't there on a normal server", async () => {
    const { calls } = setup({});
    await vi.waitFor(() => expect(calls.some((c) => c.path === "/api/replay")).toBe(true));
    expect(screen.queryByRole("region", { name: "Replay" })).toBeNull();
  });
});
