import { act, fireEvent, render, screen, within } from "@testing-library/react";
import type { DayReport, PnlHistory } from "../../api/types";
import { AppProvider, createApp } from "../../app/context";
import { fakeRest, fakeStream } from "../../test/fakes";
import { ME } from "../../test/fixtures";
import { dayOptions, downloadUrl, lastDays, reportState } from "./model";
import { Reports } from "./Reports";

const FUT = "NFO:NIFTY26OCTFUT";
const TUE: DayReport = {
  account_id: "acc-a",
  day: "2026-10-13",
  generated_at: "2026-10-13T10:30:00+00:00",
  final: true,
  priced: true,
  notes: [],
  schedule: "Kite standard plan (rates checked 2026-10-15)",
  totals: { realised_paise: 750000, unrealised_paise: 0, gross_paise: 750000, charges_paise: 111152, net_paise: 638848, turnover_paise: 376500000, trades: 2 },
  charge_lines: [
    { label: "Brokerage", paise: 4000 },
    { label: "STT", paise: 94125 },
  ],
  positions: [
    {
      instrument_id: FUT,
      product: "NRML",
      carried_qty: 150,
      reference_paise: 2505000,
      buy_qty: 0,
      buy_value_paise: 0,
      sell_qty: 150,
      sell_value_paise: 376500000,
      closing_qty: 0,
      mark_paise: 2510000,
      realised_paise: 750000,
      unrealised_paise: 0,
      gross_paise: 750000,
      charges_paise: 111152,
      net_paise: 638848,
    },
  ],
  strategies: [
    { strategy: "carried", trades: 0, gross_paise: 750000, charges_paise: 0, net_paise: 750000 },
    { strategy: "manual", trades: 2, gross_paise: 0, charges_paise: 111152, net_paise: -111152 },
  ],
  trades: [{ ts: "2026-10-13T04:30:00+00:00", client_order_id: "T1", instrument_id: FUT, side: "SELL", product: "NRML", qty: 150, price_paise: 2510000, value_paise: 376500000, strategy: "manual" }],
  rejects: [{ ts: "2026-10-13T04:35:00+00:00", detail: "T2 BUY 750 NFO:NIFTY26OCTFUT: position limit" }],
  alerts: [{ ts: "2026-10-13T04:36:00+00:00", kind: "POSITION_MISMATCH_RAISED", detail: "MockTrade shows +150, the broker 0" }],
};
const TODAY: DayReport = { ...TUE, day: "2026-10-15", final: false, totals: { ...TUE.totals, gross_paise: 0, net_paise: 0 }, positions: [], strategies: [], trades: [], rejects: [], alerts: [] };
const HISTORY: PnlHistory = {
  account_id: "acc-a",
  by: "instrument",
  from: "2026-09-15",
  to: "2026-10-15",
  rows: [{ key: FUT, gross_paise: 1350000, charges_paise: 200000, net_paise: 1150000, trades: 4 }],
  total: { gross_paise: 1350000, charges_paise: 200000, net_paise: 1150000 },
  missing_days: ["2026-10-14"],
};

function setup(params: Record<string, unknown> = {}) {
  const server = fakeRest({
    "GET /api/accounts/acc-a/reports": { today: "2026-10-15", kept: ["2026-10-12", "2026-10-13"] },
    "GET /api/accounts/acc-a/reports/2026-10-15": TODAY,
    "GET /api/accounts/acc-a/reports/2026-10-13": TUE,
    "GET /api/accounts/acc-a/pnl": HISTORY,
  });
  const { stream, socket } = fakeStream();
  const app = createApp({ rest: server.rest, stream });
  app.store.setMe(ME);
  app.store.selectAccount("acc-a");
  stream.start();
  socket().open();
  const props = { params: { link: "pink" as const, ...params }, setParams: vi.fn(), setInstrument: vi.fn(), publish: vi.fn(), open: vi.fn() };
  const view = render(
    <AppProvider app={app}>
      <Reports {...props} />
    </AppProvider>,
  );
  return { ...server, ...props, app, view };
}

describe("reports model", () => {
  it("offers today's preview first, then kept days newest first", () => {
    expect(dayOptions({ today: "2026-10-15", kept: ["2026-10-12", "2026-10-13"] }).map((o) => o.label)).toEqual([
      "2026-10-15 · today (preview)",
      "2026-10-13",
      "2026-10-12",
    ]);
    expect(dayOptions({ today: "2026-10-15", kept: ["2026-10-15"] })[0]!.label).toBe("2026-10-15 · today (kept)");
    expect(dayOptions(null)).toEqual([]);
    expect(downloadUrl("acc a", "2026-10-13", "pdf")).toBe("/api/accounts/acc%20a/reports/2026-10-13?format=pdf");
    expect(lastDays("2026-10-15")).toEqual({ start: "2026-09-15", end: "2026-10-15" });
    expect(reportState(TUE).text).toBe("Kept after the close");
    expect(reportState({ ...TUE, final: false, priced: false }).tone).toBe("bad");
  });
});

describe("Reports", () => {
  it("shows today's preview, then a kept day with its totals, sections and downloads", async () => {
    setup();
    await screen.findByText("Preview: kept at 16:00 IST with closing prices");
    fireEvent.change(screen.getByRole("combobox", { name: "Report day" }), { target: { value: "2026-10-13" } });
    await screen.findByText("Kept after the close");
    const totals = screen.getByLabelText("Day totals").textContent!;
    expect(totals).toContain("Gross +₹7,500.00");
    expect(totals).toContain("Charges ₹1,111.52");
    expect(totals).toContain("Net +₹6,388.48");
    const row = within(screen.getByRole("grid", { name: "Report positions" })).getAllByRole("row")[0]!;
    expect(row.textContent).toContain("NIFTY26OCTFUT");
    expect(row.textContent).toContain("+₹6,388.48");
    expect(within(screen.getByRole("grid", { name: "Report strategies" })).getAllByRole("row").map((r) => r.firstChild!.textContent)).toEqual(["carried", "manual"]);
    expect(screen.getByLabelText("Rejects and alerts").textContent).toContain("rejected: T2 BUY 750");
    expect(screen.getByRole("link", { name: "CSV" }).getAttribute("href")).toBe("/api/accounts/acc-a/reports/2026-10-13?format=csv");
    expect(screen.getByRole("link", { name: "PDF" }).hasAttribute("download")).toBe(true);
  });

  it("today's preview follows the day: a fill reads it again, a kept day doesn't change", async () => {
    const { calls, app } = setup();
    await screen.findByText("Preview: kept at 16:00 IST with closing prices");
    const reads = () => calls.filter((c) => c.path === "/api/accounts/acc-a/reports/2026-10-15").length;
    const before = reads();
    const bill = { total_paise: 1, turnover_paise: 99, lines: [], by_position: {}, schedule: { name: "", checked_on: "", source: "" } };
    act(() => app.store.applyCharges("acc-a", bill));
    await vi.waitFor(() => expect(reads()).toBe(before + 1));
    fireEvent.change(screen.getByRole("combobox", { name: "Report day" }), { target: { value: "2026-10-13" } });
    await screen.findByText("Kept after the close");
    const kept = () => calls.filter((c) => c.path === "/api/accounts/acc-a/reports/2026-10-13").length;
    const once = kept();
    act(() => app.store.applyCharges("acc-a", { ...bill, turnover_paise: 199 }));
    await act(async () => {});
    expect(kept()).toBe(once);
  });

  it("shows P&L history from kept reports, and says which days are missing", async () => {
    const { calls } = setup({ mode: "history", by: "instrument" });
    await screen.findByLabelText("Period totals");
    expect(calls.some((c) => c.path === "/api/accounts/acc-a/pnl?by=instrument&start=2026-09-15&end=2026-10-15")).toBe(true);
    expect(screen.getByLabelText("Period totals").textContent).toContain("Net +₹11,500.00");
    expect(screen.getByText("1 trading day without a kept report (not counted)")).toBeTruthy();
    const row = within(screen.getByRole("grid", { name: "P&L history" })).getAllByRole("row")[0]!;
    expect(row.textContent).toContain("NIFTY26OCTFUT");
  });

  it("says why a report can't be read", async () => {
    const { routes } = setup();
    routes["GET /api/accounts/acc-a/reports/2026-10-15"] = () => ({ status: 422, body: { detail: "reports need the audit trail" } });
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await act(async () => {});
    expect((await screen.findByRole("alert")).textContent).toContain("reports need the audit trail");
  });
});
