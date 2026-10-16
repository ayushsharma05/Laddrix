import { fireEvent, render, screen } from "@testing-library/react";
import { App } from "../App";
import { AppProvider, createApp } from "../app/context";
import { FakeSocket, fakeRest, fakeStream } from "../test/fakes";
import { ME } from "../test/fixtures";

const MUST = { ...ME, user: { ...ME.user, must_change_password: true } };

function setup(answer: () => { status: number; body?: unknown }) {
  let changed = false;
  const server = fakeRest({
    "GET /api/me": () => ({ status: 200, body: changed ? ME : MUST }),
    "POST /api/me/password": () => {
      const r = answer();
      if (r.status < 300) changed = true;
      return r;
    },
    "DELETE /api/session": () => ({ status: 204 }),
  });
  const { stream, socket } = fakeStream();
  const app = createApp({ rest: server.rest, stream });
  render(
    <AppProvider app={app}>
      <App />
    </AppProvider>,
  );
  const fill = (old: string, next: string, again = next) => {
    fireEvent.change(screen.getByLabelText("One-time password"), { target: { value: old } });
    fireEvent.change(screen.getByLabelText("New password"), { target: { value: next } });
    fireEvent.change(screen.getByLabelText("New password again"), { target: { value: again } });
  };
  return { ...server, app, fill, socket };
}

// the terminal (dockview) measures its panes once the password is set
beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

describe("Set your password (after an admin's reset)", () => {
  it("is all a user with a one-time password sees, and the live stream waits", async () => {
    const { calls } = setup(() => ({ status: 204 }));
    await screen.findByText("MockTrade · Set your password");
    expect(screen.queryByRole("button", { name: "Widgets ▾" })).toBeNull(); // no terminal yet
    expect(FakeSocket.all).toHaveLength(0); // no stream was opened (the server would refuse it anyway)
    expect(calls.map((c) => c.path)).toEqual(["/api/me"]);
  });

  it("the two new passwords must match before anything is sent", async () => {
    const { fill, calls } = setup(() => ({ status: 204 }));
    await screen.findByText("MockTrade · Set your password");
    fill("k7Q2mXv9pLr4tWa8", "my own long password", "my own long passwort");
    expect(screen.getByRole("status").textContent).toBe("The two new passwords differ.");
    expect((screen.getByRole("button", { name: "Set password" }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.submit(screen.getByRole("form", { name: "Set your password" })); // Enter in a field
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(0);
  });

  it("shows the server's rule in its words; on success the terminal opens", async () => {
    let refuse = true;
    const { fill, calls, app } = setup(() => (refuse ? { status: 422, body: { detail: "password must be at least 12 characters" } } : { status: 204 }));
    await screen.findByText("MockTrade · Set your password");
    fill("k7Q2mXv9pLr4tWa8", "short");
    fireEvent.click(screen.getByRole("button", { name: "Set password" }));
    expect((await screen.findByRole("alert")).textContent).toBe("password must be at least 12 characters");
    refuse = false;
    fill("k7Q2mXv9pLr4tWa8", "my own long password");
    fireEvent.click(screen.getByRole("button", { name: "Set password" }));
    await vi.waitFor(() => expect(app.store.getState().me?.user.must_change_password).toBeFalsy());
    expect(calls.filter((c) => c.method === "POST").at(-1)!.body).toEqual({ old: "k7Q2mXv9pLr4tWa8", new: "my own long password" });
    expect(screen.queryByText("MockTrade · Set your password")).toBeNull();
    expect(FakeSocket.all).toHaveLength(1); // now the live stream starts
  });

  it("can sign out instead", async () => {
    const { app, calls } = setup(() => ({ status: 204 }));
    await screen.findByText("MockTrade · Set your password");
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await vi.waitFor(() => expect(app.store.getState().me).toBeNull());
    expect(calls.some((c) => c.method === "DELETE" && c.path === "/api/session")).toBe(true);
  });
});
