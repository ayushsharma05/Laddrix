import type { Spread } from "../../api/types";
import { addLeg, bodyOf, controlsBody, controlText, draftOf, emptyDraft, formula, legSummary, MAX_LEGS, missing, quoteLeg, removeLeg, setLeg } from "./model";

const name = (id: string) => id.split(":")[1]!;
const FLY: Spread = {
  spread_id: "s1",
  instrument_id: "SPREAD:s1",
  name: "fly",
  tick_paise: 10,
  created_at: "",
  updated_at: "",
  legs: [
    { instrument_id: "NFO:A", ratio: 1, multiplier: 1, quote: true, payup_ticks: 2 },
    { instrument_id: "NFO:B", ratio: -2, multiplier: -2, quote: false, payup_ticks: 3 },
    { instrument_id: "NFO:C", ratio: 1, multiplier: 1, quote: false, payup_ticks: 2 },
  ],
};

describe("the Autospreader's form", () => {
  it("starts as a calendar: buy the first leg, sell the second, quote the first", () => {
    expect(emptyDraft().legs.map((l) => [l.ratio, l.multiplier, l.quote])).toEqual([
      [1, 1, true],
      [-1, -1, false],
    ]);
  });

  it("round-trips a spread into a draft and back into the body the server takes", () => {
    expect(bodyOf({ ...draftOf(FLY), name: "  fly " })).toEqual({ name: "fly", legs: FLY.legs });
  });

  it("carries the price multiplier with the ratio while they were equal", () => {
    let d = setLeg(draftOf(FLY), 1, { ratio: -3 });
    expect(d.legs[1]).toMatchObject({ ratio: -3, multiplier: -3 });
    d = setLeg(d, 1, { multiplier: -5 });
    d = setLeg(d, 1, { ratio: -1 }); // now they differ: the multiplier stays as set
    expect(d.legs[1]).toMatchObject({ ratio: -1, multiplier: -5 });
  });

  it("quotes exactly one leg", () => {
    const d = quoteLeg(draftOf(FLY), 2);
    expect(d.legs.map((l) => l.quote)).toEqual([false, false, true]);
  });

  it("adds legs up to six and removes down to two, keeping a quoting leg", () => {
    let d = draftOf(FLY);
    for (let i = 0; i < 5; i += 1) d = addLeg(d);
    expect(d.legs).toHaveLength(MAX_LEGS);
    d = removeLeg(draftOf(FLY), 0); // the quoting leg goes: the first left quotes
    expect(d.legs.map((l) => [l.instrumentId, l.quote])).toEqual([
      ["NFO:B", true],
      ["NFO:C", false],
    ]);
    expect(removeLeg(d, 0).legs).toHaveLength(2); // two is the least
  });

  it("asks for each leg's contract before anything is sent", () => {
    expect(missing(emptyDraft())).toEqual(["leg 1: pick its contract", "leg 2: pick its contract"]);
    expect(missing(draftOf(FLY))).toEqual([]);
  });

  it("says what the spread buys and sells, and its price formula", () => {
    expect(legSummary(FLY.legs, name)).toBe("+1 A −2 B +1 C");
    expect(formula(FLY.legs, name)).toBe("A − 2×B + C");
    expect(formula([{ ...FLY.legs[1]! }, { ...FLY.legs[0]! }], name)).toBe("−2×B + A");
  });
});

describe("the spread's controls (#425)", () => {
  const SPECS = [
    { name: "chase_ticks", label: "Chase", kind: "ticks", help: "", required: false, default: 0, min: 0, max: 50, choices: [] },
    { name: "hedge_timeout_s", label: "Hedge timeout", kind: "seconds", help: "", required: false, default: 5, min: 1, max: 300, choices: [] },
  ];
  it("show what was typed, else the spread's own, else the server's default", () => {
    const d = emptyDraft();
    expect(controlText(d, SPECS[0]!)).toBe("0");
    expect(controlText({ ...d, controls: { chase_ticks: "" } }, SPECS[0]!)).toBe("");
    expect(controlText(draftOf({ ...FLY, controls: { chase_ticks: 3 } }), SPECS[0]!)).toBe("3");
  });
  it("are sent as numbers, or each problem in words", () => {
    expect(controlsBody(SPECS, { ...emptyDraft(), controls: { chase_ticks: "2", hedge_timeout_s: "7.5" } })).toEqual({ controls: { chase_ticks: 2, hedge_timeout_s: 7.5 }, problems: [] });
    expect(controlsBody(SPECS, { ...emptyDraft(), controls: { chase_ticks: "60", hedge_timeout_s: "x" } }).problems).toEqual(["Chase must be at most 50", "Hedge timeout must be a number"]);
    expect(controlsBody(SPECS, { ...emptyDraft(), controls: { chase_ticks: "" } })).toEqual({ controls: { hedge_timeout_s: 5 }, problems: [] }); // empty: left to the server
  });
});
