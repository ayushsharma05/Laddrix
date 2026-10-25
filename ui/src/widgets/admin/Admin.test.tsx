import { act, fireEvent, render, screen, within } from "@testing-library/react";
import type { AdminAccount, AdminUser, LimitsSaved } from "../../api/types";
import { AppProvider, createApp } from "../../app/context";
import { fakeRest, fakeStream } from "../../test/fakes";
import { ME } from "../../test/fixtures";
import { Admin } from "./Admin";
import { LIMITS, SPEC } from "./testdata";

const URI = "otpauth://totp/Laddrix:meera?secret=JBSWY3DPEHPK3PXP&issuer=Laddrix";
const ROLES = [
  { name: "admin", help: "manage users, accounts and limits; stop trading" },
  { name: "trader", help: "place and manage orders" },
  { name: "risk", help: "see every account; no orders" },
];

function user(id: string, extra: Partial<AdminUser> = {}): AdminUser {
  return { id, name: id.toUpperCase(), roles: ["trader"], active: true, must_change_password: false, locked_until: null, failures: 0, sessions: 0, ...extra };
}

function account(id: string, extra: Partial<AdminAccount> = {}): AdminAccount {
  return { id, broker: "paper", broker_client_id: "P1", owner: "ayush", traders: [], active: true, running: true, not_running: "", ...extra };
}

/** A small server: the users and accounts change as the screen changes them. */
function world(roles: string[] = ["admin", "trader"]) {
  const users = [user("ayush", { roles: ["admin", "trader"], sessions: 1 }), user("riya", { failures: 3 })];
  const accounts = [account("acc-a"), account("acc-k", { broker: "kite", running: false, not_running: "starts after a restart" })];
  const preview = (looser: boolean): LimitsSaved => ({
    account_id: "acc-a",
    limits: { ...LIMITS, max_order_lots: looser ? 20 : 5 },
    running: true,
    changes: [{ name: "max_order_lots", label: "Most lots per order", old: 10, new: looser ? 20 : 5, text: `Most lots per order: 10 → ${looser ? 20 : 5}`, looser }],
    warnings: looser ? [] : ["NFO:NIFTY26OCTFUT: 8 lots held, above the new 5-lot limit: only orders that reduce it will pass"],
    applied: false,
  });
  const server = fakeRest({
    "GET /api/admin/users": () => ({ status: 200, body: users }),
    "GET /api/admin/roles": ROLES,
    "POST /api/admin/users": (body: unknown) => {
      const b = body as { id: string; name: string; roles: AdminUser["roles"] };
      if (users.some((u) => u.id === b.id)) return { status: 409, body: { detail: `user ${b.id} already exists` } };
      const made = user(b.id, { name: b.name, roles: b.roles, must_change_password: true });
      users.push(made);
      return { status: 201, body: { user: made, temporary_password: "k7Q2mXv9pLr4tWa8", totp_uri: URI, note: "shown once: give both to the user" } };
    },
    "PATCH /api/admin/users/ayush": (body: unknown) =>
      (body as { roles?: string[] }).roles?.includes("admin") === false ? { status: 409, body: { detail: "ayush is the last active admin: make another admin first" } } : { status: 200, body: users[0] },
    "PATCH /api/admin/users/riya": (body: unknown) => {
      Object.assign(users[1]!, body);
      return { status: 200, body: users[1] };
    },
    "POST /api/admin/users/riya/unlock": () => {
      users[1]!.failures = 0;
      return { status: 200, body: users[1] };
    },
    "POST /api/admin/users/riya/reset-password": () => ({ status: 200, body: { temporary_password: "Zz9yX8wV7uT6sR5q", note: "shown once" } }),
    "GET /api/admin/accounts": () => ({ status: 200, body: { accounts, brokers: ["paper"] } }),
    "POST /api/admin/accounts": (body: unknown) => {
      const b = body as { id: string; owner: string; broker_client_id: string };
      accounts.push(account(b.id, { owner: b.owner, broker_client_id: b.broker_client_id }));
      return { status: 201, body: accounts.at(-1) };
    },
    "PATCH /api/admin/accounts/acc-a": (body: unknown) => {
      Object.assign(accounts[0]!, body);
      return { status: 200, body: accounts[0] };
    },
    "GET /api/admin/limits/spec": SPEC,
    "GET /api/admin/accounts/acc-a/limits": { account_id: "acc-a", limits: LIMITS, running: true },
    "PUT /api/admin/accounts/acc-a/limits": (body: unknown) => {
      const lots = (body as { limits: Record<string, unknown> }).limits.max_order_lots as number;
      return { status: 200, body: { ...preview(lots > 10), applied: true } };
    },
  });
  // the preview answers by the lots asked (the dry run has its own path with the query)
  server.routes["PUT /api/admin/accounts/acc-a/limits?dry_run=true"] = (body: unknown) => ({ status: 200, body: preview(((body as { limits: Record<string, number> }).limits.max_order_lots ?? 0) > 10) });
  const { stream } = fakeStream();
  const app = createApp({ rest: server.rest, stream });
  app.store.setMe({ ...ME, user: { ...ME.user, roles } });
  return { ...server, app, users, accounts };
}

function show(w: ReturnType<typeof world>, params: Record<string, unknown> = {}) {
  const props = { params: { link: "none" as const, ...params }, setParams: vi.fn(), setInstrument: vi.fn(), publish: vi.fn(), open: vi.fn() };
  render(
    <AppProvider app={w.app}>
      <Admin {...props} />
    </AppProvider>,
  );
  return props;
}

const rowsOf = async (name: string) => within(await screen.findByRole("grid", { name })).getAllByRole("row");
const sent = (w: ReturnType<typeof world>, method: string, path: string) => w.calls.filter((c) => c.method === method && c.path === path);

describe("Admin: users", () => {
  it("lists users with their state; a new user's one-time password and authenticator are shown once, with a QR code", async () => {
    const w = world();
    show(w);
    const [ayush, riya] = await rowsOf("Users");
    expect(ayush!.textContent).toContain("admin trader");
    expect(ayush!.textContent).toContain("on · 1 signed in");
    expect(within(ayush!).queryByRole("button", { name: "Turn off…" })).toBeNull(); // not yourself
    expect(within(riya!).getByRole("button", { name: "Unlock" })).toBeTruthy(); // 3 failed logins
    fireEvent.click(screen.getByRole("button", { name: "+ New user" }));
    const form = screen.getByRole("form", { name: "New user" });
    fireEvent.change(within(form).getByLabelText("User id"), { target: { value: " meera " } });
    fireEvent.change(within(form).getByLabelText("Name"), { target: { value: "Meera" } });
    expect(within(form).getByTitle("see every account; no orders")).toBeTruthy(); // the server's words for each role
    fireEvent.click(within(within(form).getByRole("group", { name: "Roles" })).getByLabelText(/risk/));
    fireEvent.click(within(form).getByRole("button", { name: "Add user" }));
    const once = await screen.findByRole("region", { name: "Shown once" });
    expect(sent(w, "POST", "/api/admin/users")[0]!.body).toEqual({ id: "meera", name: "Meera", roles: ["trader", "risk"] });
    expect(within(once).getByLabelText("One-time password").textContent).toBe("k7Q2mXv9pLr4tWa8");
    expect(within(once).getByLabelText("Authenticator key").textContent).toBe("JBSW Y3DP EHPK 3PXP");
    expect(within(once).getByRole("img", { name: "Authenticator QR code" })).toBeTruthy();
    expect((await rowsOf("Users")).at(-1)!.textContent).toContain("must set a password");
    fireEvent.click(within(once).getByRole("button", { name: "Done: I've handed these over" }));
    expect(screen.queryByText("k7Q2mXv9pLr4tWa8")).toBeNull(); // gone for good
  });

  it("says the server's refusals in its words: the last admin, a taken id", async () => {
    const w = world();
    show(w);
    const [ayush] = await rowsOf("Users");
    fireEvent.click(within(ayush!).getByRole("button", { name: "Roles…" }));
    fireEvent.click(within(within(ayush!).getByRole("group", { name: "Roles" })).getByLabelText(/admin/));
    fireEvent.click(within(ayush!).getByRole("button", { name: "Save" }));
    expect((await screen.findByRole("alert")).textContent).toBe("ayush is the last active admin: make another admin first");
    expect(sent(w, "PATCH", "/api/admin/users/ayush")[0]!.body).toEqual({ roles: ["trader"] });
    fireEvent.click(screen.getByRole("button", { name: "+ New user" }));
    fireEvent.change(screen.getByLabelText("User id"), { target: { value: "riya" } });
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "R" } });
    fireEvent.click(screen.getByRole("button", { name: "Add user" }));
    expect((await screen.findByText("user riya already exists")).getAttribute("role")).toBe("alert");
  });

  it("turning off and a new password take a second click; unlock doesn't", async () => {
    const w = world();
    show(w);
    let riya = (await rowsOf("Users"))[1]!;
    fireEvent.click(within(riya).getByRole("button", { name: "Turn off…" }));
    expect(sent(w, "PATCH", "/api/admin/users/riya")).toHaveLength(0); // armed only
    fireEvent.click(within(riya).getByRole("button", { name: "Confirm turn off" }));
    await vi.waitFor(() => expect(sent(w, "PATCH", "/api/admin/users/riya")[0]!.body).toEqual({ active: false }));
    await vi.waitFor(async () => expect((await rowsOf("Users"))[1]!.textContent).toContain("off"));
    riya = (await rowsOf("Users"))[1]!;
    fireEvent.click(within(riya).getByRole("button", { name: "Unlock" }));
    await vi.waitFor(() => expect(sent(w, "POST", "/api/admin/users/riya/unlock")).toHaveLength(1));
    riya = (await rowsOf("Users"))[1]!;
    fireEvent.click(within(riya).getByRole("button", { name: "New password…" }));
    fireEvent.click(within(riya).getByRole("button", { name: "Confirm new password" }));
    const once = await screen.findByRole("region", { name: "Shown once" });
    expect(within(once).getByLabelText("One-time password").textContent).toBe("Zz9yX8wV7uT6sR5q");
    expect(within(once).queryByRole("img")).toBeNull(); // a password only: no authenticator
  });

  it("risk users read but change nothing; others see nothing", async () => {
    const w = world(["risk"]);
    show(w);
    const [ayush] = await rowsOf("Users");
    expect(within(ayush!).queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "+ New user" })).toBeNull();
    expect(screen.getByText("read only (risk)")).toBeTruthy();
  });

  it("a trader gets a plain note and nothing is asked of the server", () => {
    const w = world(["trader"]);
    show(w);
    expect(screen.getByText("For admin and risk users.")).toBeTruthy();
    expect(w.calls).toHaveLength(0);
  });
});

describe("Admin: accounts", () => {
  it("lists accounts and why one isn't running; adds one; turning off takes a second click", async () => {
    const w = world();
    const props = show(w, { tab: "accounts" });
    const [a, k] = await rowsOf("Accounts");
    expect(a!.textContent).toContain("running");
    expect(k!.textContent).toContain("not running: starts after a restart");
    fireEvent.click(screen.getByRole("button", { name: "+ New account" }));
    const form = screen.getByRole("form", { name: "New account" });
    expect(within(within(form).getByLabelText("Broker")).getAllByRole("option").map((o) => o.textContent)).toEqual(["paper"]); // what this server can add
    fireEvent.change(within(form).getByLabelText("Account id"), { target: { value: "acc-meera" } });
    fireEvent.change(within(form).getByLabelText("Client id"), { target: { value: "PAPER9" } });
    fireEvent.change(within(form).getByLabelText("Owner"), { target: { value: "riya" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add account" }));
    await vi.waitFor(async () => expect(await rowsOf("Accounts")).toHaveLength(3));
    expect(sent(w, "POST", "/api/admin/accounts")[0]!.body).toEqual({ id: "acc-meera", broker: "paper", broker_client_id: "PAPER9", owner: "riya", traders: [] });
    const first = (await rowsOf("Accounts"))[0]!;
    fireEvent.click(within(first).getByRole("button", { name: "Turn off…" }));
    expect(sent(w, "PATCH", "/api/admin/accounts/acc-a")).toHaveLength(0);
    fireEvent.click(within(first).getByRole("button", { name: "Confirm turn off" }));
    await vi.waitFor(async () => expect((await rowsOf("Accounts"))[0]!.textContent).toContain("off: closing trades only"));
    fireEvent.click(within((await rowsOf("Accounts"))[0]!).getByRole("button", { name: "Traders…" }));
    fireEvent.click(within(screen.getByRole("group", { name: "Traders" })).getByLabelText(/riya/));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await vi.waitFor(() => expect(sent(w, "PATCH", "/api/admin/accounts/acc-a").at(-1)!.body).toEqual({ traders: ["riya"] }));
    fireEvent.click(within((await rowsOf("Accounts"))[0]!).getByRole("button", { name: "Limits" }));
    expect(props.setParams).toHaveBeenCalledWith({ tab: "limits", accountId: "acc-a" });
  });
});

describe("Admin: limits", () => {
  const field = (label: string) => screen.getByLabelText(label) as HTMLInputElement;

  it("draws the form from the spec, shows what changes and its warnings, and saves only what was checked", async () => {
    const w = world();
    show(w, { tab: "limits", accountId: "acc-a" });
    await screen.findByRole("group", { name: "Limits of acc-a" });
    expect(field("Most value per order").value).toBe("50,00,000");
    expect(within(screen.getByRole("group", { name: "Exchanges" })).getAllByRole("checkbox").map((c) => (c as HTMLInputElement).checked)).toEqual([false, false, true, true]); // BFO BSE NFO NSE
    expect(screen.getByText("Price band", { selector: "legend" })).toBeTruthy();
    fireEvent.change(field("Most lots per order"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Check changes" }));
    const what = await screen.findByRole("region", { name: "What changes" });
    expect(what.textContent).toContain("Most lots per order: 10 → 5");
    expect(what.textContent).not.toContain("looser");
    expect(what.textContent).toContain("only orders that reduce it will pass");
    fireEvent.click(screen.getByRole("button", { name: "Save" })); // tighter: one click
    expect(await screen.findByRole("status")).toHaveProperty("textContent", "Saved: the limits apply from the next order.");
    expect(sent(w, "PUT", "/api/admin/accounts/acc-a/limits")[0]!.body).toEqual({ limits: expect.objectContaining({ max_order_lots: 5, max_order_value_paise: 50_00_000_00 }) });
  });

  it("a looser limit takes a second click, and editing after a check asks for a new check", async () => {
    const w = world();
    show(w, { tab: "limits", accountId: "acc-a" });
    await screen.findByRole("group", { name: "Limits of acc-a" });
    fireEvent.change(field("Most lots per order"), { target: { value: "20" } });
    fireEvent.click(screen.getByRole("button", { name: "Check changes" }));
    expect((await screen.findByRole("region", { name: "What changes" })).textContent).toContain("· looser");
    fireEvent.click(screen.getByRole("button", { name: "Save… (1 looser)" }));
    expect(sent(w, "PUT", "/api/admin/accounts/acc-a/limits")).toHaveLength(0);
    fireEvent.change(field("Most lots per order"), { target: { value: "25" } }); // changed again
    expect(screen.queryByRole("region", { name: "What changes" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Confirm/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Check changes" }));
    await screen.findByRole("region", { name: "What changes" });
    fireEvent.click(screen.getByRole("button", { name: "Save… (1 looser)" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm: loosen and save" }));
    await vi.waitFor(() => expect(sent(w, "PUT", "/api/admin/accounts/acc-a/limits")).toHaveLength(1));
    expect(sent(w, "PUT", "/api/admin/accounts/acc-a/limits")[0]!.body).toEqual({ limits: expect.objectContaining({ max_order_lots: 20 }) }); // what was checked
  });

  it("problems are said before anything is sent; risk users see the limits but can't edit", async () => {
    const w = world();
    show(w, { tab: "limits", accountId: "acc-a" });
    await screen.findByRole("group", { name: "Limits of acc-a" });
    fireEvent.change(field("Most lots per order"), { target: { value: "0" } });
    for (const c of within(screen.getByRole("group", { name: "Exchanges" })).getAllByRole("checkbox")) if ((c as HTMLInputElement).checked) fireEvent.click(c);
    fireEvent.click(screen.getByRole("button", { name: "Check changes" }));
    expect(screen.getByRole("alert").textContent).toBe("Exchanges: pick at least 1Most lots per order must be at least 1");
    expect(w.calls.filter((c) => c.method === "PUT")).toHaveLength(0);
  });

  it("read only for risk", async () => {
    const w = world(["risk"]);
    show(w, { tab: "limits", accountId: "acc-a" });
    await screen.findByRole("group", { name: "Limits of acc-a" });
    expect(field("Most lots per order").disabled).toBe(true);
    expect(screen.queryByRole("button", { name: "Check changes" })).toBeNull();
  });
});

describe("Admin: status (#287)", () => {
  const STATUS = {
    state: "down",
    ready: false,
    other_firms_not_ready: 1,
    at: "2026-10-24T04:00:00+00:00",
    parts: [
      { name: "server", state: "ready", reason: "answering", since: "2026-10-24T03:45:00+00:00", account_id: null },
      { name: "recovery acc-a", state: "starting", reason: "rebuilding orders and positions after a restart", since: null, account_id: "acc-a" },
      { name: "broker login acc-a", state: "degraded", reason: "Log in to Kite for acc-a (AB1234): the market opens in 5 min", since: "2026-10-24T03:40:00+00:00", account_id: "acc-a" },
      { name: "prices", state: "down", reason: "No prices for over a minute during market hours", since: "2026-10-24T03:59:00+00:00", account_id: null },
    ],
  };

  it("shows each part as the server says it, the worst first in the header, and asks again every 10 s", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const w = world();
    w.routes["GET /api/status"] = STATUS;
    show(w, { tab: "status" });
    const header = await screen.findByRole("status", { name: "Server status" });
    expect(header.textContent).toContain("down");
    expect(header.textContent).toContain("not ready: Docker and the monitor see it unhealthy");
    expect(header.textContent).toContain("(1 of another firm's parts not ready)"); // never whose
    const prices = screen.getByLabelText("prices");
    expect(prices.textContent).toBe("pricesdownNo prices for over a minute during market hourssince 09:29:00");
    expect(within(prices).getByText("down").className).toBe("tone-bad");
    expect(within(screen.getByLabelText("broker login acc-a")).getByText("degraded").className).toBe("tone-warn");
    expect(within(screen.getByLabelText("recovery acc-a")).getByText("starting").className).toBe("tone-pending");
    expect(screen.getByLabelText("server").textContent).toContain("since 09:15:00");
    const asked = () => sent(w, "GET", "/api/status").length;
    expect(asked()).toBe(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(asked()).toBe(2);
    vi.useRealTimers();
  });

  it("a refusal is said in the server's words", async () => {
    const w = world(["risk"]);
    w.routes["GET /api/status"] = () => ({ status: 403, body: { detail: "the status page is for admin and risk users" } });
    show(w, { tab: "status" });
    expect((await screen.findByText("the status page is for admin and risk users")).className).toContain("tone-bad");
  });
});
