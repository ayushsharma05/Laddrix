import type { AdminAccount, AdminUser } from "../../api/types";
import { accountState, limitGroups, limitInputs, moneyText, parseLimit, parseLimits, parseRupees, totpParts, userState } from "./model";
import { LIMITS, SPEC } from "./testdata";

describe("money in the limits form", () => {
  it("reads rupees the way people type them, in whole paise, never floats", () => {
    expect(parseRupees("50,00,000")).toBe(50_00_000_00);
    expect(parseRupees("₹ 1,000.5")).toBe(1_000_50);
    expect(parseRupees("0.07")).toBe(7);
    expect(parseRupees("12")).toBe(12_00);
    for (const bad of ["", "abc", "1.234", "-5", "1e5", "1,00.0.0"]) expect(parseRupees(bad)).toBeNull();
    expect(moneyText(50_00_000_00)).toBe("50,00,000");
    expect(moneyText(1_000_50)).toBe("1,000.50");
  });
});

describe("the limits form from the spec", () => {
  it("starts from the limits in force, each kind as it is edited", () => {
    const inputs = limitInputs(SPEC, LIMITS);
    expect(inputs).toMatchObject({
      allowed_exchanges: ["NFO", "NSE"],
      blocked_instruments: [],
      allow_option_selling: false,
      max_order_lots: "10",
      max_order_value_paise: "50,00,000",
      "price_band_pct.EQ": "5",
      duplicate_window_s: "1",
    });
    expect(inputs.allowed_exchanges).not.toBe(LIMITS.allowed_exchanges); // a copy: edits don't touch the answer
    expect(limitInputs(SPEC, {}).max_order_lots).toBe("10"); // a limit the server didn't send: its default
  });

  it("sends exactly what the server expects, or every problem in words", () => {
    const inputs = { ...limitInputs(SPEC, LIMITS), max_order_lots: "3", max_order_value_paise: "1,00,000", allowed_exchanges: ["NSE", "NSE", "BSE"] };
    const { limits, problems } = parseLimits(SPEC, inputs);
    expect(problems).toEqual([]);
    expect(limits).toMatchObject({ max_order_lots: 3, max_order_value_paise: 1_00_000_00, allowed_exchanges: ["BSE", "NSE"], allow_option_selling: false, "price_band_pct.EQ": 5 });
    const bad = parseLimits(SPEC, { ...inputs, max_order_lots: "0", max_order_value_paise: "lots", allowed_exchanges: [], duplicate_window_s: "x", "price_band_pct.EQ": "" });
    expect(bad.limits).toBeNull();
    expect(bad.problems).toEqual([
      "Exchanges: pick at least 1",
      "Most lots per order must be at least 1",
      "Most value per order: not an amount in rupees",
      "Price band: EQ is required",
      "Double-click window must be a number",
    ]);
  });

  it("checks each kind against the spec's own range and choices", () => {
    const p = (name: string) => SPEC.params.find((x) => x.name === name)!;
    expect(parseLimit(p("max_order_lots"), "10001")).toEqual({ ok: false, problem: "Most lots per order must be at most 10000" });
    expect(parseLimit(p("max_order_lots"), "2.5")).toEqual({ ok: false, problem: "Most lots per order must be a whole number" });
    expect(parseLimit(p("max_order_lots"), "1,000")).toEqual({ ok: true, value: 1000 });
    expect(parseLimit(p("max_order_value_paise"), "999")).toEqual({ ok: false, problem: "Most value per order must be at least ₹1,000.00" });
    expect(parseLimit(p("allowed_exchanges"), ["LSE"])).toEqual({ ok: false, problem: "Exchanges: LSE isn't one of BFO, BSE, NFO, NSE" });
    expect(parseLimit(p("blocked_instruments"), Array.from({ length: 201 }, (_, i) => `NSE:S${i}`))).toEqual({ ok: false, problem: "Blocked contracts: at most 200" });
    expect(parseLimit(p("allow_option_selling"), true)).toEqual({ ok: true, value: true });
    expect(parseLimit(p("duplicate_window_s"), "0")).toEqual({ ok: true, value: 0 });
    expect(parseLimit({ ...p("max_order_lots"), kind: "colour" }, "red")).toEqual({ ok: false, problem: "Most lots per order: this screen can't edit a colour yet" });
  });

  it("keeps the price bands together", () => {
    const groups = limitGroups(SPEC).map((g) => [g.title, g.params.length]);
    expect(groups).toContainEqual(["Price band", 2]);
    expect(groups.filter(([t]) => t === "")).toHaveLength(SPEC.params.length - 2);
  });
});

describe("states in words", () => {
  const user: AdminUser = { id: "riya", name: "Riya", roles: ["trader"], active: true, must_change_password: false, locked_until: null, failures: 0, sessions: 2 };
  const now = Date.parse("2026-10-16T10:00:00Z");
  it("users", () => {
    expect(userState(user, now)).toEqual({ text: "on · 2 signed in", tone: "good" });
    expect(userState({ ...user, sessions: 0 }, now).text).toBe("on");
    expect(userState({ ...user, must_change_password: true }, now)).toEqual({ text: "must set a password", tone: "pending" });
    expect(userState({ ...user, locked_until: "2026-10-16T10:10:00Z" }, now)).toEqual({ text: "locked (failed logins)", tone: "bad" });
    expect(userState({ ...user, locked_until: "2026-10-16T09:50:00Z" }, now).tone).toBe("good"); // the lock ran out
    expect(userState({ ...user, active: false, locked_until: "2026-10-16T10:10:00Z" }, now)).toEqual({ text: "off", tone: "dim" });
  });
  it("accounts", () => {
    const a: AdminAccount = { id: "acc", broker: "paper", broker_client_id: "P1", owner: "riya", traders: [], active: true, running: true, not_running: "" };
    expect(accountState(a)).toEqual({ text: "running", tone: "good" });
    expect(accountState({ ...a, running: false, not_running: "starts after a restart" })).toEqual({ text: "not running: starts after a restart", tone: "pending" });
    expect(accountState({ ...a, active: false, running: false })).toEqual({ text: "off: closing trades only", tone: "dim" });
  });
});

describe("the authenticator link", () => {
  it("gives the key for typing in by hand, grouped in fours", () => {
    expect(totpParts("otpauth://totp/MockTrade:meera?secret=JBSWY3DPEHPK3PXPABCD&issuer=MockTrade")).toEqual({ account: "meera", issuer: "MockTrade", secret: "JBSW Y3DP EHPK 3PXP ABCD" });
    expect(totpParts("https://evil.example/?secret=X")).toBeNull();
    expect(totpParts("not a link")).toBeNull();
    expect(totpParts("otpauth://totp/MockTrade:x")).toBeNull(); // no secret
  });
});
