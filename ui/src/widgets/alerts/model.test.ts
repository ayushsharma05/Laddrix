import { buildAlertBody, describe as say, paramText, stateTone, upsert } from "./model";
import { FUT, SPEC, alert } from "./testdata";

const input = (extra: Partial<Parameters<typeof buildAlertBody>[1]> = {}) => ({
  kind: SPEC.kinds[0],
  instrumentId: FUT,
  values: { level_paise: "25,100" },
  repeat: "once",
  cooldown: "300",
  note: " breakout ",
  webhook: true,
  ...extra,
});

describe("alerts model", () => {
  it("the request from the server's spec: prices to paise, the note trimmed", () => {
    expect(buildAlertBody(SPEC, input())).toEqual({
      body: { kind: "PRICE_ABOVE", instrument_id: FUT, params: { level_paise: 2510000 }, repeat: "once", cooldown_s: 300, note: "breakout", webhook: true },
      problems: [],
    });
    const pct = buildAlertBody(SPEC, input({ kind: SPEC.kinds[1], values: { pct: "1.5", direction: "down" } }));
    expect(pct.body!.params).toEqual({ pct: 1.5, direction: "down" });
    const spread = buildAlertBody(SPEC, input({ kind: SPEC.kinds[2], values: { other: "NFO:NIFTY26NOVFUT", level_paise: "-12.5" } }));
    expect(spread.body!.params).toEqual({ other: "NFO:NIFTY26NOVFUT", level_paise: -1250 });
  });

  it("every problem is said and nothing is built", () => {
    const { body, problems } = buildAlertBody(SPEC, input({ kind: undefined, instrumentId: null, repeat: "always", cooldown: "5", note: "x".repeat(201) }));
    expect(body).toBeNull();
    expect(problems).toEqual(["Pick what to watch", "Pick a contract", "Repeat must be one of once, every", "Cool-down must be 10–86400 seconds", "The note can be at most 200 characters"]);
    expect(buildAlertBody(SPEC, input({ values: { level_paise: "" } })).problems).toEqual(["Price is required"]);
    expect(buildAlertBody(SPEC, input({ kind: SPEC.kinds[1], values: { pct: "0", direction: "up" } })).problems).toEqual(["Move must be at least 0.01"]);
    expect(buildAlertBody(SPEC, input({ cooldown: "3.5" })).problems).toEqual(["Cool-down must be 10–86400 seconds"]);
    expect(buildAlertBody(SPEC, input({ cooldown: "86401" })).problems).toEqual(["Cool-down must be 10–86400 seconds"]);
  });

  it("an alert in words, by the kinds of its settings", () => {
    expect(say(SPEC, alert("A1"))).toBe("Price crosses above 25,100.00");
    expect(say(SPEC, alert("A2", { kind: "PCT_MOVE", params: { pct: 2, direction: "up" } }))).toBe("Moves from the previous close 2% · up");
    expect(say(SPEC, alert("A3", { kind: "SPREAD_ABOVE", params: { other: "NFO:NIFTY26NOVFUT", level_paise: -1500 } }))).toBe("Spread crosses above NIFTY26NOVFUT · −15.00");
    expect(say(SPEC, alert("A4", { kind: "NEW_KIND" }))).toBe("NEW_KIND"); // a kind this screen hasn't seen
    expect(paramText({ name: "volume", label: "Volume", kind: "count", help: "", required: true, default: null, min: null, max: null, choices: [] }, 150000)).toBe("1,50,000");
    expect(paramText(SPEC.kinds[0]!.params[0]!, undefined)).toBe("–");
  });

  it("state tones and the live list", () => {
    expect(stateTone(alert("A1"))).toBe("dim");
    expect(stateTone(alert("A1", { done: true, status: "fired" }))).toBe("good");
    expect(stateTone(alert("A1", { status: "fired: arms again once it is false again" }))).toBe("good");
    expect(stateTone(alert("A1", { status: "already true: fires the next time it crosses" }))).toBe("pending");
    expect(stateTone(alert("A1", { enabled: false }))).toBe("dim");
    const list = [alert("A1"), alert("A3")];
    expect(upsert(list, alert("A2")).map((a) => a.alert_id)).toEqual(["A1", "A2", "A3"]);
    expect(upsert(list, alert("A3", { fired_count: 4 }))[1]!.fired_count).toBe(4);
  });
});
