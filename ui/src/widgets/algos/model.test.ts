import type { AlgoParamSpec, AlgoSpec } from "../../api/types";
import { algo } from "../../test/fixtures";
import { buildAlgoBody, figures, initialValues, parseOffset, parseParam, sortAlgos, stateTone } from "./model";

function param(name: string, kind: string, extra: Partial<AlgoParamSpec> = {}): AlgoParamSpec {
  return { name, label: name, kind, help: "", required: true, default: null, min: null, max: null, choices: [], ...extra };
}

const SPREAD: AlgoSpec = {
  code: "SPREAD",
  name: "Spreader",
  help: "",
  params: [param("leg2", "instrument"), param("level_paise", "price_offset"), param("payup_ticks", "ticks", { default: 2, min: 0, max: 50 })],
};


describe("algo form", () => {
  it("starts from each parameter's default, prices shown in rupees", () => {
    const spec: AlgoSpec = { ...SPREAD, params: [...SPREAD.params, param("limit_paise", "price", { required: false, default: 2501000 }), param("cross", "flag", { default: true })] };
    expect(initialValues(spec)).toEqual({ leg2: "", level_paise: "", payup_ticks: "2", limit_paise: "25010.00", cross: true });
  });

  it("reads signed prices for offsets, plain prices otherwise", () => {
    expect(parseOffset("-1.5")).toBe(-150);
    expect(parseOffset("0")).toBe(0);
    expect(parseOffset("1,200.25")).toBe(120025);
    expect(parseOffset("abc")).toBeNull();
    expect(parseParam(param("p", "price"), "-5")).toEqual({ ok: false, problem: "p: not a price" });
    expect(parseParam(param("p", "price_offset"), "-5")).toEqual({ ok: true, value: -500 });
  });

  it("checks kinds, required and limits; leaves out empty optional settings", () => {
    expect(parseParam(param("n", "count"), "2.5")).toEqual({ ok: false, problem: "n must be a whole number" });
    expect(parseParam(param("n", "seconds"), "2.5")).toEqual({ ok: true, value: 2.5 });
    expect(parseParam(param("n", "count"), " ")).toEqual({ ok: false, problem: "n is required" });
    expect(parseParam(param("n", "count", { required: false }), "")).toEqual({ ok: true, skip: true });
    expect(parseParam(param("n", "ticks", { max: 50 }), "51")).toEqual({ ok: false, problem: "n must be at most 50" });
    expect(parseParam(param("lim", "price", { min: 100 }), "0.5")).toEqual({ ok: false, problem: "lim must be at least 1.00" });
    expect(parseParam(param("m", "choice", { choices: ["join", "cross"] }), "lift")).toEqual({ ok: false, problem: "m must be one of join, cross" });
    expect(parseParam(param("f", "flag"), false)).toEqual({ ok: true, value: false });
    expect(parseParam(param("x", "some_new_kind"), "abc")).toEqual({ ok: true, value: "abc" }); // the server decides
  });

  it("builds the start body: lots to quantity, params by name", () => {
    const values = { leg2: "NFO:NIFTY26NOVFUT", level_paise: "-12.5", payup_ticks: "3" };
    const { body, problems } = buildAlgoBody({ spec: SPREAD, values, instrumentId: "NFO:NIFTY26OCTFUT", side: "SELL", lots: 2, lotSize: 75, product: "NRML" });
    expect(problems).toEqual([]);
    expect(body).toStrictEqual({
      code: "SPREAD",
      instrument_id: "NFO:NIFTY26OCTFUT",
      side: "SELL",
      qty: 150,
      product: "NRML",
      params: { leg2: "NFO:NIFTY26NOVFUT", level_paise: -1250, payup_ticks: 3 },
    });
  });

  it("an empty optional setting is left out, not sent empty", () => {
    const spec: AlgoSpec = { code: "S", name: "S", help: "", params: [param("limit_paise", "price", { required: false })] };
    const { body } = buildAlgoBody({ spec, values: { limit_paise: "" }, instrumentId: "X:Y", side: "BUY", lots: 1, lotSize: 1, product: "" });
    expect(Object.keys(body!.params)).toEqual([]);
    expect("product" in body!).toBe(false); // the server's default
  });

  it("lists every problem at once", () => {
    const { body, problems } = buildAlgoBody({ spec: SPREAD, values: { payup_ticks: "2" }, instrumentId: null, side: "BUY", lots: 0, lotSize: 75, product: "" });
    expect(body).toBeNull();
    expect(problems).toEqual(["Pick a contract", "Lots must be at least 1", "leg2 is required", "level_paise is required"]);
  });
});

describe("algo grid", () => {
  it("running algos first, newest first", () => {
    const list = sortAlgos({
      a: algo("a", { state: "DONE", created_at: "2026-10-14T05:00:00+00:00" }),
      b: algo("b", { created_at: "2026-10-14T04:00:00+00:00" }),
      c: algo("c", { state: "PAUSED", created_at: "2026-10-14T04:30:00+00:00" }),
    });
    expect(list.map((a) => a.algo_id)).toEqual(["c", "b", "a"]);
    expect(list.map(stateTone)).toEqual(["dim", "buy", "good"]);
  });

  it("an algo's own figures are labelled and formatted by their names", () => {
    const f = figures({ leg2: "NFO:NIFTY26NOVFUT", unhedged: 75, traded_spread_paise: -1250, max_legging_ms: 4, gone: null });
    expect(f).toEqual([
      { label: "Leg2", text: "NIFTY26NOVFUT", alert: false },
      { label: "Unhedged", text: "75", alert: true },
      { label: "Traded spread", text: "−12.50", alert: false },
      { label: "Max legging", text: "4 ms", alert: false },
    ]);
  });
});
