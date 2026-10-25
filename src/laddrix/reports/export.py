"""A day's report as a download: CSV (for a spreadsheet) or PDF (to read and keep).

Both are made from the report as the API sends it (report_to_dict), so the screen, the CSV
and the PDF can never disagree. Money in the CSV is rupees with two decimals (a spreadsheet
adds them up); the PDF is laid out for reading.
"""

import csv
import io
from typing import Any

from laddrix.reports.pdf import text_pdf


def rupees(paise: int | None) -> str:
    if paise is None:
        return ""
    sign = "-" if paise < 0 else ""
    whole, part = divmod(abs(paise), 100)
    return f"{sign}{whole}.{part:02d}"


def report_csv(r: dict[str, Any]) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    t = r["totals"]
    w.writerow(["End-of-day report", r["account_id"], r["day"],
                "final" if r["final"] else "preview", r["generated_at"]])  # fmt: skip
    for note in r["notes"]:
        w.writerow(["note", note])
    w.writerow([])
    w.writerow(["Summary", "rupees"])
    for label, key in (("Realised", "realised_paise"), ("Unrealised", "unrealised_paise"),
                       ("Gross P&L", "gross_paise"), ("Charges", "charges_paise"),
                       ("Net P&L", "net_paise"), ("Turnover", "turnover_paise")):  # fmt: skip
        w.writerow([label, rupees(t[key])])
    w.writerow(["Trades", t["trades"]])
    w.writerow([])
    w.writerow(["Charges", "rupees", r["schedule"]])
    for line in r["charge_lines"]:
        w.writerow([line["label"], rupees(line["paise"])])
    w.writerow([])
    w.writerow(["Positions", "product", "carried in", "reference", "bought", "buy value", "sold",
                "sell value", "carried forward", "last price", "realised", "unrealised", "gross",
                "charges", "net"])  # fmt: skip
    for p in r["positions"]:
        w.writerow([p["instrument_id"], p["product"], p["carried_qty"],
                    rupees(p["reference_paise"]), p["buy_qty"], rupees(p["buy_value_paise"]),
                    p["sell_qty"], rupees(p["sell_value_paise"]), p["closing_qty"],
                    rupees(p["mark_paise"]), rupees(p["realised_paise"]),
                    rupees(p["unrealised_paise"]), rupees(p["gross_paise"]),
                    rupees(p["charges_paise"]), rupees(p["net_paise"])])  # fmt: skip
    w.writerow([])
    w.writerow(["Strategy", "trades", "gross", "charges", "net"])
    for s in r["strategies"]:
        w.writerow([s["strategy"], s["trades"], rupees(s["gross_paise"]),
                    rupees(s["charges_paise"]), rupees(s["net_paise"])])  # fmt: skip
    w.writerow([])
    w.writerow(["Trades", "order", "instrument", "side", "product", "qty", "price", "value",
                "strategy"])  # fmt: skip
    for x in r["trades"]:
        w.writerow([x["ts"], x["client_order_id"], x["instrument_id"], x["side"],
                    x["product"], x["qty"], rupees(x["price_paise"]),
                    rupees(x["value_paise"]), x["strategy"]])  # fmt: skip
    w.writerow([])
    w.writerow(["Rejected", "detail"])
    for n in r["rejects"]:
        w.writerow([n["ts"], n["detail"]])
    w.writerow([])
    w.writerow(["Alerts", "kind", "detail"])
    for n in r["alerts"]:
        w.writerow([n["ts"], n["kind"], n["detail"]])
    return out.getvalue()


def report_lines(r: dict[str, Any]) -> list[str]:
    t = r["totals"]
    m = lambda p: f"{rupees(p):>14}"  # noqa: E731
    lines = [
        f"END-OF-DAY REPORT   {r['account_id']}   {r['day']}   "
        f"{'final' if r['final'] else 'preview (before the close)'}",
        f"generated {r['generated_at']}",
        *[f"note: {n}" for n in r["notes"]],
        "",
        f"Gross P&L {m(t['gross_paise'])}   (realised {rupees(t['realised_paise'])}, "
        f"unrealised {rupees(t['unrealised_paise'])})",
        f"Charges   {m(t['charges_paise'])}   {r['schedule']}",
        f"Net P&L   {m(t['net_paise'])}",
        f"Turnover  {m(t['turnover_paise'])}   trades {t['trades']}",
        "",
        "CHARGES   "
        + "  ".join(
            f"{c['label']} {rupees(c['paise'])}" for c in r["charge_lines"] if c["paise"]
        ),  # fmt: skip
        "",
        f"{'POSITION':<26}{'PROD':<6}{'IN':>7}{'BOUGHT':>8}{'SOLD':>8}{'OUT':>8}{'LAST':>12}"
        f"{'GROSS':>14}{'CHARGES':>11}{'NET':>14}",
    ]
    for p in r["positions"]:
        lines.append(f"{p['instrument_id']:<26}{p['product']:<6}{p['carried_qty']:>7}"
                     f"{p['buy_qty']:>8}{p['sell_qty']:>8}{p['closing_qty']:>8}"
                     f"{rupees(p['mark_paise']) or '-':>12}{rupees(p['gross_paise']):>14}"
                     f"{rupees(p['charges_paise']):>11}{rupees(p['net_paise']):>14}")  # fmt: skip
    lines += ["", f"{'STRATEGY':<40}{'TRADES':>8}{'GROSS':>14}{'CHARGES':>11}{'NET':>14}"]
    for s in r["strategies"]:
        lines.append(f"{s['strategy']:<40}{s['trades']:>8}{rupees(s['gross_paise']):>14}"
                     f"{rupees(s['charges_paise']):>11}{rupees(s['net_paise']):>14}")  # fmt: skip
    lines += ["", f"{'TIME (UTC)':<27}{'ORDER':<14}{'INSTRUMENT':<26}{'SIDE':<5}{'PROD':<6}"
                  f"{'QTY':>7}{'PRICE':>12}{'STRATEGY':>20}"]  # fmt: skip
    for x in r["trades"]:
        lines.append(f"{x['ts'][:26]:<27}{x['client_order_id']:<14}{x['instrument_id']:<26}"
                     f"{x['side']:<5}{x['product']:<6}{x['qty']:>7}{rupees(x['price_paise']):>12}"
                     f"{x['strategy']:>20}")  # fmt: skip
    lines += ["", "REJECTED"] + [f"  {n['ts'][:19]}  {n['detail']}" for n in r["rejects"]]
    lines += ["", "ALERTS"] + [f"  {n['ts'][:19]}  {n['kind']}: {n['detail']}" for n in r["alerts"]]
    return [line[:150] for line in lines]


def report_pdf(r: dict[str, Any]) -> bytes:
    return text_pdf(report_lines(r), title=f"Laddrix {r['account_id']} {r['day']}")
