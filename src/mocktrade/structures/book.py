"""Structure positions (#428, part of #250): spreads held, their P&L, and what is left over.

A structure is a spread held in one account: its legs (from a spread definition) and the
fills allocated to it. Fills themselves never change; a structure only says which part of
which order's fills belongs to it.

    an Autospreader's fills ─► its spread's structure, at once (auto)
    a trader's allocation   ─► add (part of) an order's fills, remove them, or move them to
                               another structure (that is how structures split and combine)
    every change            ─► a note in the account's audit trail (STRUCTURE_*), replayed at
                               start: nothing is kept anywhere else

Rules:
- An order's filled quantity can't be allocated more than once in all: allocated ≤ filled.
- Only an order on one of the structure's legs can go into it.
- Removing or moving takes back only what is there; the totals of all structures together
  (each leg's quantity, realised P&L, cost) never change by moving.
- What a structure holds is worked out from its allocations each time, at each order's own
  average price, in the order the orders were placed (weighted average cost, as positions).

What it shows, per structure:
    spread lots   the whole spreads its legs make (+ bought, − sold; 0 if they don't match)
    each leg      its quantity, average, P&L, and its residual beyond the whole spreads
    the spread    average Σ price× · leg average; mark Σ price× · last; P&L Σ of its legs
And per instrument, what is unallocated: the account's net position less every structure's.
P&L is gross, as the position book's: charges are not taken off.
"""

import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from fractions import Fraction
from typing import Any

from mocktrade.domain.orders import Side
from mocktrade.orders.model import EventKind, OrderEvent, OrderView
from mocktrade.positions.book import _Position

NOTES = ("STRUCTURE_CREATED", "STRUCTURE_DELETED", "STRUCTURE_ALLOCATED")
MAX_STRUCTURES = 200  # per account
MAX_NAME = 40


class StructureError(Exception):
    def __init__(self, reason: str, *, missing: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.missing = missing


@dataclass(frozen=True)
class StructureLeg:
    instrument_id: str
    ratio: int  # lots of this leg per spread lot (+ bought with the spread)
    multiplier: int  # how much its price counts in the spread's price


@dataclass
class Structure:
    structure_id: str
    name: str
    legs: tuple[StructureLeg, ...]
    spread_id: str | None  # the spread it was made from (an Autospreader's: auto)
    created_by: str
    created_at: datetime
    allocated: dict[str, int] = field(default_factory=dict)  # client order id -> qty


def _round(value: Fraction) -> int:
    return int(value.numerator * 2 + value.denominator) // (2 * value.denominator)


class StructureBook:
    def __init__(
        self,
        account_id: str,
        *,
        order: Callable[[str], OrderView | None],  # an order of this account by its id
        orders: Callable[[], list[OrderView]],  # every order of this account today
        net_qty: Callable[[str], int],  # the account's net position in an instrument
        lot_size: Callable[[str], int],
        ltp: Callable[[str], int | None],
        spread_of: Callable[[str], Any] = lambda _parent_id: None,  # an ASE's SpreadDef
        note: Callable[[str, str, dict[str, Any]], None] | None = None,  # to the audit
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.account_id = account_id
        self._order = order
        self._orders = orders
        self._net_qty = net_qty
        self._lot = lot_size
        self._ltp = ltp
        self._spread_of = spread_of
        self._note = note
        self._clock = clock
        self._structures: dict[str, Structure] = {}
        self._auto: dict[str, int] = {}  # client order id -> qty allocated automatically

    # --- reading the audit trail back ---------------------------------------------------------

    def load(self, notes: list[Any]) -> None:
        """Rebuild from the account's STRUCTURE_* notes (oldest first)."""
        for e in notes:
            x = e.extra
            if e.kind == "STRUCTURE_CREATED":
                legs = tuple(StructureLeg(str(g["instrument_id"]), int(g["ratio"]),
                                          int(g["multiplier"])) for g in x["legs"])  # fmt: skip
                made = x.get("created_at")
                self._structures[x["structure_id"]] = Structure(
                    x["structure_id"],
                    x["name"],
                    legs,
                    x.get("spread_id"),
                    x["by"],
                    datetime.fromisoformat(made) if made else e.ts,
                )
            elif e.kind == "STRUCTURE_DELETED":
                self._structures.pop(x["structure_id"], None)
            elif e.kind == "STRUCTURE_ALLOCATED":
                s = self._structures.get(x["structure_id"])
                if s is None:
                    continue
                self._add(s, x["client_order_id"], int(x["qty"]))
                if x.get("auto"):
                    cid = x["client_order_id"]
                    self._auto[cid] = self._auto.get(cid, 0) + int(x["qty"])

    # --- changing them ------------------------------------------------------------------------

    def create(self, name: str, legs: list[StructureLeg], user_id: str,
               spread_id: str | None = None) -> Structure:  # fmt: skip
        clean = name.strip()
        if not clean or len(clean) > MAX_NAME:
            raise StructureError(f"the name must be 1 to {MAX_NAME} characters")
        if not legs:
            raise StructureError("a structure needs its legs (make it from a spread)")
        if len(self._structures) >= MAX_STRUCTURES:
            raise StructureError(f"at most {MAX_STRUCTURES} structures: delete an empty one")
        sid = secrets.token_hex(4)
        s = Structure(sid, clean, tuple(legs), spread_id, user_id, self._clock())
        self._structures[sid] = s
        self._record("STRUCTURE_CREATED", f"{clean}: " + " ".join(
            f"{g.ratio:+d} {g.instrument_id}" for g in legs), {
            "structure_id": sid, "name": clean, "spread_id": spread_id, "by": user_id,
            "created_at": s.created_at.isoformat(),
            "legs": [{"instrument_id": g.instrument_id, "ratio": g.ratio,
                      "multiplier": g.multiplier} for g in legs]})  # fmt: skip
        return s

    def delete(self, structure_id: str, user_id: str) -> None:
        s = self._get(structure_id)
        if any(s.allocated.values()):
            raise StructureError(f"{s.name} still holds fills: move or remove them first")
        del self._structures[structure_id]
        self._record("STRUCTURE_DELETED", s.name, {"structure_id": structure_id, "by": user_id})

    def allocate(self, structure_id: str, client_order_id: str, qty: int, user_id: str) -> None:
        """Put `qty` of an order's fills into a structure."""
        s = self._get(structure_id)
        self._check_add(s, client_order_id, qty)
        self._allocate(s, client_order_id, qty, user_id)

    def remove(self, structure_id: str, client_order_id: str, qty: int, user_id: str) -> None:
        """Take `qty` of an order's fills back out of a structure (they are unallocated)."""
        s = self._get(structure_id)
        self._check_remove(s, client_order_id, qty)
        self._allocate(s, client_order_id, -qty, user_id)

    def move(self, from_id: str, to_id: str, client_order_id: str, qty: int,
             user_id: str) -> None:  # fmt: skip
        """Move `qty` of an order's fills from one structure to another, both or neither."""
        a, b = self._get(from_id), self._get(to_id)
        if a is b:
            raise StructureError("moving needs two different structures")
        self._check_remove(a, client_order_id, qty)
        self._check_leg(b, client_order_id)
        self._allocate(a, client_order_id, -qty, user_id)
        self._allocate(b, client_order_id, qty, user_id)

    def on_order_event(self, event: OrderEvent) -> None:
        """An Autospreader's fills go to its spread's structure at once (made at its first
        fill). Only what the order filled beyond what was already put there: a replayed or
        repeated update adds nothing."""
        if event.kind is not EventKind.FILL or event.order is None:
            return
        o = event.order
        if o.request.parent_id is None:
            return
        spread = self._spread_of(o.request.parent_id)
        if spread is None:
            return
        delta = o.filled_qty - self._auto.get(o.client_order_id, 0)
        if delta <= 0:
            return
        s = next((x for x in self._structures.values() if x.spread_id == spread.spread_id), None)
        if s is None:
            legs = [StructureLeg(g.instrument_id, g.ratio, g.multiplier) for g in spread.legs]
            s = self.create(spread.name, legs, o.request.user_id, spread.spread_id)
        if o.request.instrument_id not in {g.instrument_id for g in s.legs}:
            return
        delta = min(delta, self._unallocated_of(o))
        if delta <= 0:
            return
        self._auto[o.client_order_id] = self._auto.get(o.client_order_id, 0) + delta
        self._allocate(s, o.client_order_id, delta, "ASE", auto=True)

    # --- what they hold ---------------------------------------------------------------------

    def structures(self) -> list[dict[str, Any]]:
        ordered = sorted(self._structures.values(), key=lambda x: (x.created_at, x.structure_id))
        return [self._view(s) for s in ordered]

    def get(self, structure_id: str) -> dict[str, Any]:
        return self._view(self._get(structure_id))

    def unallocated(self) -> list[dict[str, Any]]:
        """Per instrument: the account's net position less what structures hold."""
        held: dict[str, int] = {}
        for s in self._structures.values():
            for leg, pos in self._legs(s).items():
                held[leg] = held.get(leg, 0) + pos.qty
        instruments = set(held) | {o.request.instrument_id for o in self._orders()}
        out = []
        for i in sorted(instruments):
            rest = self._net_qty(i) - held.get(i, 0)
            if rest:
                out.append({"instrument_id": i, "qty": rest})
        return out

    def free_fills(self) -> list[dict[str, Any]]:
        """Orders with fills not in any structure yet: what a trader may allocate."""
        out = []
        for o in self._orders():
            free = self._unallocated_of(o)
            if free > 0:
                out.append({"client_order_id": o.client_order_id,
                            "instrument_id": o.request.instrument_id,
                            "side": o.request.side.value, "filled_qty": o.filled_qty,
                            "unallocated_qty": free,
                            "avg_price_paise": o.avg_price_paise})  # fmt: skip
        return out

    # --- inside -------------------------------------------------------------------------------

    def _get(self, structure_id: str) -> Structure:
        s = self._structures.get(structure_id)
        if s is None:
            raise StructureError(f"no structure {structure_id}", missing=True)
        return s

    def _allocated_of(self, client_order_id: str) -> int:
        return sum(s.allocated.get(client_order_id, 0) for s in self._structures.values())

    def _unallocated_of(self, o: OrderView) -> int:
        return o.filled_qty - self._allocated_of(o.client_order_id)

    def _check_leg(self, s: Structure, client_order_id: str) -> OrderView:
        o = self._order(client_order_id)
        if o is None:
            raise StructureError(f"no order {client_order_id} in this account")
        if o.request.instrument_id not in {g.instrument_id for g in s.legs}:
            raise StructureError(f"{o.request.instrument_id} is not a leg of {s.name}")
        return o

    def _check_add(self, s: Structure, client_order_id: str, qty: int) -> None:
        o = self._check_leg(s, client_order_id)
        if qty <= 0:
            raise StructureError("the quantity must be above 0")
        lot = self._lot(o.request.instrument_id)
        if qty % lot:
            raise StructureError(f"{qty} is not a whole number of lots ({lot} each)")
        free = self._unallocated_of(o)
        if qty > free:
            raise StructureError(f"{client_order_id} has {free} filled not yet in a structure, "
                                 f"not {qty}")  # fmt: skip

    def _check_remove(self, s: Structure, client_order_id: str, qty: int) -> None:
        if qty <= 0:
            raise StructureError("the quantity must be above 0")
        there = s.allocated.get(client_order_id, 0)
        if qty > there:
            raise StructureError(f"{s.name} holds {there} of {client_order_id}, not {qty}")

    def _allocate(self, s: Structure, client_order_id: str, qty: int, user_id: str,
                  auto: bool = False) -> None:  # fmt: skip
        self._add(s, client_order_id, qty)
        verb = "added to" if qty > 0 else "taken out of"
        self._record("STRUCTURE_ALLOCATED", f"{abs(qty)} of {client_order_id} {verb} {s.name}",
                     {"structure_id": s.structure_id, "client_order_id": client_order_id,
                      "qty": qty, "by": user_id, "auto": auto})  # fmt: skip

    @staticmethod
    def _add(s: Structure, client_order_id: str, qty: int) -> None:
        now = s.allocated.get(client_order_id, 0) + qty
        if now:
            s.allocated[client_order_id] = now
        else:
            s.allocated.pop(client_order_id, None)

    def _record(self, kind: str, detail: str, extra: dict[str, Any]) -> None:
        if self._note is not None:
            self._note(kind, detail, extra)

    def _legs(self, s: Structure) -> dict[str, _Position]:
        """Each leg's position from the allocations, at each order's own average price, in
        the order the orders were placed."""
        legs = {g.instrument_id: _Position() for g in s.legs}
        orders = [(o, q) for cid, q in s.allocated.items()
                  if (o := self._order(cid)) is not None and q > 0]  # fmt: skip
        orders.sort(key=lambda x: (x[0].created_at, x[0].client_order_id))
        for o, qty in orders:
            price = Fraction(o.filled_value_paise or o.avg_price_paise * o.filled_qty,
                             o.filled_qty)  # fmt: skip
            _apply(legs[o.request.instrument_id], o.request.side, qty, price)
        return legs

    def _view(self, s: Structure) -> dict[str, Any]:
        legs = self._legs(s)
        # whole spreads: every leg's quantity the same number of times its ratio, one sign
        counts = []
        for g in s.legs:
            per = abs(g.ratio) * self._lot(g.instrument_id)
            q = legs[g.instrument_id].qty
            same_way = q * (1 if g.ratio > 0 else -1)  # + when it is the spread bought
            counts.append(same_way // per if same_way >= 0 else -((-same_way) // per))
        positive = all(c >= 0 for c in counts)
        negative = all(c <= 0 for c in counts)
        if positive:
            lots = min(counts)
        elif negative:
            lots = max(counts)
        else:
            lots = 0
        out_legs = []
        avg_spread = Fraction(0)
        mark: Fraction | None = Fraction(0)
        realised = unrealised = Fraction(0)
        for g in s.legs:
            pos = legs[g.instrument_id]
            ltp = self._ltp(g.instrument_id)
            un = (Fraction(ltp) - pos.avg) * pos.qty if ltp is not None and pos.qty else Fraction(0)
            realised += pos.realised
            unrealised += un
            avg_spread += g.multiplier * pos.avg
            mark = None if mark is None or ltp is None else mark + g.multiplier * ltp
            whole = lots * abs(g.ratio) * self._lot(g.instrument_id) * (1 if g.ratio > 0 else -1)
            out_legs.append({
                "instrument_id": g.instrument_id, "ratio": g.ratio, "multiplier": g.multiplier,
                "qty": pos.qty, "avg_price_paise": _round(pos.avg), "ltp_paise": ltp,
                "realised_paise": _round(pos.realised), "unrealised_paise": _round(un),
                "residual_qty": pos.qty - whole,
            })  # fmt: skip
        allocations = []
        for cid, qty in sorted(s.allocated.items()):
            o = self._order(cid)
            allocations.append({"client_order_id": cid, "qty": qty,
                                "instrument_id": o.request.instrument_id if o else None,
                                "side": o.request.side.value if o else None,
                                "avg_price_paise": o.avg_price_paise if o else None})  # fmt: skip
        mixed = not positive and not negative
        return {
            "structure_id": s.structure_id, "name": s.name, "spread_id": s.spread_id,
            "created_by": s.created_by, "created_at": s.created_at.isoformat(),
            "lots": lots,
            "avg_spread_paise": _round(avg_spread) if lots else None,
            "mark_paise": None if mark is None else _round(mark),
            "realised_paise": _round(realised), "unrealised_paise": _round(unrealised),
            "pnl_paise": _round(realised + unrealised),
            "note": ("its legs point opposite ways: no whole spread; see each leg"
                     if mixed else ""),
            "legs": out_legs, "allocations": allocations,
        }  # fmt: skip


def _apply(pos: _Position, side: Side, qty: int, price: Fraction) -> None:
    """One order's allocated part into a leg at that order's average price, an exact
    fraction (weighted average cost, as the position book)."""
    signed = side.sign * qty
    if pos.qty == 0 or (pos.qty > 0) == (signed > 0):
        pos.qty += signed
        pos.cost += signed * price
        return
    closing = min(qty, abs(pos.qty))
    avg = pos.avg
    direction = 1 if pos.qty > 0 else -1
    pos.realised += (price - avg) * closing * direction
    pos.qty -= direction * closing
    pos.cost = avg * pos.qty
    rest = qty - closing
    if rest:
        pos.qty = side.sign * rest
        pos.cost = price * pos.qty
