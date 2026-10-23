import { act, fireEvent, render, screen } from "@testing-library/react";
import { AppProvider, createApp } from "../app/context";
import { fakeRest, fakeStream } from "../test/fakes";
import { ME } from "../test/fixtures";
import { browserName, SessionsDialog } from "./SessionsDialog";
import { StepUpDialog } from "./StepUpDialog";

const PATH = "/api/admin/accounts/acc-a/limits";
const LIMITS = `PUT ${PATH}`;
const ASK = { status: 428, body: { detail: "step-up: enter a fresh authenticator code to loosen limits" } };

function setup(routes: Record<string, unknown>, ui = <StepUpDialog />) {
  const server = fakeRest(routes);
  const { stream } = fakeStream();
  const app = createApp({ rest: server.rest, stream });
  app.store.setMe(ME);
  render(<AppProvider app={app}>{ui}</AppProvider>);
  return { ...server, app };
}

describe("step-up for privileged changes (#363)", () => {
  it("asks for a code, sends it, then repeats the change once", async () => {
    let stepped = false;
    const { rest, calls } = setup({
      [LIMITS]: () => (stepped ? { status: 200, body: { applied: true } } : ASK),
      "POST /api/me/step-up": (body: unknown) => {
        if ((body as { code: string }).code !== "123456") return { status: 403, body: { detail: "wrong code" } };
        stepped = true;
        return { status: 204 };
      },
    });
    const change = rest.put(PATH, { limits: {} });
    expect(await screen.findByText("Enter a fresh authenticator code to loosen limits.")).toBeTruthy();
    const input = screen.getByLabelText("Authenticator code");
    fireEvent.change(input, { target: { value: "111111" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "Wrong code: wait for the next one and try again");
    fireEvent.change(input, { target: { value: "12a3456" } }); // digits only
    expect((input as HTMLInputElement).value).toBe("123456");
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    await expect(change).resolves.toEqual({ applied: true });
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual([LIMITS, "POST /api/me/step-up", "POST /api/me/step-up", LIMITS]);
    await vi.waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("cancelling fails the change with the server's reason", async () => {
    const { rest, calls } = setup({ [LIMITS]: () => ASK });
    const change = rest.put(PATH, { limits: {} }).catch((e: unknown) => e);
    fireEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    expect(((await change) as Error).message).toBe("step-up: enter a fresh authenticator code to loosen limits");
    expect(calls).toHaveLength(1); // nothing sent again
  });

  it("asks only once per change: a second 428 is an error", async () => {
    const { rest } = setup({ [LIMITS]: () => ASK, "POST /api/me/step-up": () => ({ status: 204 }) });
    const change = rest.put(PATH, { limits: {} }).catch((e: unknown) => e);
    fireEvent.change(await screen.findByLabelText("Authenticator code"), { target: { value: "123456" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
    expect(((await change) as { status: number }).status).toBe(428);
  });

  it("without the shell, a 428 is an error like any other", async () => {
    const { rest } = setup({ [LIMITS]: () => ASK }, <></>);
    const err = (await rest.put(PATH, {}).catch((e: unknown) => e)) as { status: number };
    expect(err.status).toBe(428);
  });
});

describe("your sessions (#363)", () => {
  const rows = [
    { id: "aaaa", created_at: "2026-10-23T04:00:00+00:00", last_seen: "2026-10-23T05:00:00+00:00", device: "Mozilla/5.0 (Windows NT 10.0) Chrome/130.0 Safari/537.36", ip: "10.0.0.1", current: true },
    { id: "bbbb", created_at: "2026-10-22T04:00:00+00:00", last_seen: "2026-10-22T05:00:00+00:00", device: "Mozilla/5.0 (iPhone; CPU iPhone OS 17) Safari/604.1", ip: "10.0.0.9", current: false },
  ];

  it("lists them and ends one, never the current one", async () => {
    let left = rows;
    const { calls } = setup(
      {
        "GET /api/me/sessions": () => ({ status: 200, body: left }),
        "DELETE /api/me/sessions/bbbb": () => {
          left = rows.slice(0, 1);
          return { status: 204 };
        },
      },
      <SessionsDialog onClose={() => {}} />,
    );
    expect(await screen.findByText("Chrome on Windows")).toBeTruthy();
    expect(screen.getByText("Safari on iOS")).toBeTruthy();
    expect(screen.getByText("this one")).toBeTruthy();
    const ends = screen.getAllByRole("button", { name: "End" });
    expect(ends).toHaveLength(1); // only the other one
    await act(async () => fireEvent.click(ends[0]!));
    await vi.waitFor(() => expect(screen.queryByText("Safari on iOS")).toBeNull());
    expect(calls.some((c) => c.method === "DELETE" && c.path === "/api/me/sessions/bbbb")).toBe(true);
  });

  it("names browsers plainly", () => {
    expect(browserName("")).toBe("unknown");
    expect(browserName("Mozilla/5.0 (Macintosh; Intel Mac OS X 14) Firefox/131.0")).toBe("Firefox on macOS");
    expect(browserName("Mozilla/5.0 (X11; Linux x86_64) Chrome/130 Electron/32.0 Safari/537")).toBe("MockTrade desktop on Linux");
    expect(browserName("python-httpx/0.27")).toBe("python-httpx");
  });
});
