// Answers fetched for one thing are never shown for another (#450): each hook keeps its answer
// with what it was for, so after a switch it shows nothing until the new answer comes, never
// the previous account's algos, the previous contract's GTT rules or the previous order's
// charges. Each second request here is refused (404): the hook must stay empty.
import { act, render } from "@testing-library/react";
import { AppProvider, createApp } from "../app/context";
import { fakeRest, fakeStream } from "../test/fakes";
import { ME } from "../test/fixtures";
import { useAlgoSpecs } from "./algos/AlgoManager";
import { useGttSpec } from "./gtt/GttBook";
import { ESTIMATE_DELAY_MS, useChargeEstimate } from "./ticket/useChargeEstimate";

const OCT = "NFO:NIFTY26OCTFUT";
const NOV = "NFO:NIFTY26NOVFUT";

function world(routes: Record<string, unknown>) {
  const { stream } = fakeStream();
  const app = createApp({ rest: fakeRest(routes).rest, stream });
  app.store.setMe(ME);
  return app;
}

/** Renders `use(input)` and records what it returns after each render. */
function probe<I, T>(app: ReturnType<typeof createApp>, use: (input: I) => T, first: I) {
  const seen: T[] = [];
  function Probe({ input }: { input: I }) {
    seen.push(use(input));
    return null;
  }
  const view = render(<AppProvider app={app}><Probe input={first} /></AppProvider>);
  const change = (input: I) => view.rerender(<AppProvider app={app}><Probe input={input} /></AppProvider>);
  return { seen, change };
}

describe("an answer is kept with what it was for", () => {
  it("useAlgoSpecs: never the previous account's algos", async () => {
    const app = world({ "GET /api/accounts/acc-a/algo-specs": [{ code: "SLICER", name: "Slicer", help: "", family: "execution", params: [] }] });
    const { seen, change } = probe(app, (id: string) => useAlgoSpecs(id).specs, "acc-a" as string);
    await vi.waitFor(() => expect(seen.at(-1)?.map((s) => s.code)).toEqual(["SLICER"]));
    seen.length = 0;
    change("acc-view");
    await act(async () => {});
    expect(seen.length).toBeGreaterThan(0);
    expect(seen.every((s) => s === null)).toBe(true);
  });

  it("useGttSpec: never the previous contract's rules", async () => {
    const spec = { instrument_id: OCT, kinds: ["single"], legs: { single: 1 }, labels: { single: "Single" }, tick_paise: 10, lot_size: 75, products: ["NRML"], min_distance_pct: 0.25, expiry_days: 365, max_active: 250, help: {} };
    const app = world({ [`GET /api/accounts/acc-a/gtt-spec?instrument_id=${encodeURIComponent(OCT)}`]: spec });
    const { seen, change } = probe(app, (id: string) => useGttSpec("acc-a", id).spec, OCT as string);
    await vi.waitFor(() => expect(seen.at(-1)?.instrument_id).toBe(OCT));
    seen.length = 0;
    change(NOV);
    await act(async () => {});
    expect(seen.length).toBeGreaterThan(0);
    expect(seen.every((s) => s === null)).toBe(true);
  });

  it("useChargeEstimate: never the charges of a different order", async () => {
    vi.useFakeTimers();
    try {
      const bill = { lines: [], total_paise: 4200, schedule: { name: "test", checked_on: "2026-10-01" } };
      const app = world({ "POST /api/accounts/acc-a/charges/estimate": bill });
      const order = (qty: number) => ({ instrumentId: OCT, side: "BUY" as const, qty, pricePaise: 2501000, product: "NRML" });
      const { seen, change } = probe(app, (o: ReturnType<typeof order>) => useChargeEstimate("acc-a", o), order(75));
      await act(async () => vi.advanceTimersByTime(ESTIMATE_DELAY_MS + 10));
      await act(async () => {});
      expect(seen.at(-1)).toMatchObject({ total_paise: 4200 });
      seen.length = 0;
      change(order(150)); // another order: its estimate isn't in yet
      expect(seen.length).toBeGreaterThan(0);
      expect(seen.every((s) => s === null)).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });
});
