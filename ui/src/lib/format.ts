// How numbers, money and times are written on screen. Money arrives in paise (whole numbers),
// so nothing here does float arithmetic on rupees: we split paise into rupees and paise as integers.
// Grouping is the Indian way (12,34,567.00). Negative numbers use the real minus sign "−" (U+2212),
// which is as wide as "+" so columns of signed numbers line up.

const MINUS = "−";

/** Group whole digits the Indian way: last three, then twos. "1234567" -> "12,34,567". */
function groupIndian(digits: string): string {
  if (digits.length <= 3) return digits;
  let head = digits.slice(0, -3);
  const groups = [digits.slice(-3)];
  while (head.length > 2) {
    groups.unshift(head.slice(-2));
    head = head.slice(0, -2);
  }
  groups.unshift(head);
  return groups.join(",");
}

/** Paise to "rupees.paise" without a sign: 2501010 -> "25,010.10". */
function unsignedRupees(paise: number): string {
  const whole = Math.round(Math.abs(paise));
  const rupees = Math.floor(whole / 100);
  const rest = whole % 100;
  return `${groupIndian(String(rupees))}.${String(rest).padStart(2, "0")}`;
}

function signOf(n: number, always: boolean): string {
  if (n < 0) return MINUS;
  return always && n > 0 ? "+" : "";
}

/** A price in paise, as traders read it: 2501010 -> "25,010.10", -500 -> "−5.00". */
export function price(paise: number): string {
  return signOf(Math.round(paise), false) + unsignedRupees(paise);
}

/** Money with the rupee sign: 562725000 -> "₹56,27,250.00". `sign: true` adds "+" to gains. */
export function rupees(paise: number, opts: { sign?: boolean } = {}): string {
  return `${signOf(Math.round(paise), opts.sign ?? false)}₹${unsignedRupees(paise)}`;
}

/** A quantity: 150000 -> "1,50,000"; negative (short) -> "−75". */
export function qty(n: number): string {
  return signOf(n, false) + groupIndian(String(Math.abs(Math.trunc(n))));
}

/** Change from the previous close in rupees, always signed: "+95.30", "−100.00", "0.00".
 *  Empty when there is no close to compare with. */
export function change(ltpPaise: number, closePaise: number | null | undefined): string {
  if (closePaise == null) return "";
  const diff = ltpPaise - closePaise;
  return signOf(diff, true) + unsignedRupees(diff);
}

/** Change from the previous close in percent, two decimals, always signed: "+0.38%".
 *  Empty when there is no usable close (missing or zero). */
export function changePct(ltpPaise: number, closePaise: number | null | undefined): string {
  if (closePaise == null || closePaise === 0) return "";
  const pct = ((ltpPaise - closePaise) / closePaise) * 100;
  const shown = Math.abs(pct).toFixed(2);
  // A change that rounds to 0.00 shows no sign: "+0.00%" would claim a direction we can't see.
  if (shown === "0.00") return "0.00%";
  return `${signOf(pct, true)}${shown}%`;
}

/** Volume, short: 950 -> "950", 12500 -> "12.5K", 125000 -> "1.25L", 23000000 -> "2.3Cr". */
export function shortVolume(n: number): string {
  const abs = Math.abs(n);
  const sign = n < 0 ? MINUS : "";
  const short = (value: number, unit: string) => `${sign}${trim(value.toFixed(2))}${unit}`;
  if (abs >= 1_00_00_000) return short(abs / 1_00_00_000, "Cr");
  if (abs >= 1_00_000) return short(abs / 1_00_000, "L");
  if (abs >= 1_000) return short(abs / 1_000, "K");
  return sign + String(abs);
}

/** "12.50" -> "12.5", "3.00" -> "3": no trailing zeros in short volumes. */
function trim(fixed: string): string {
  return fixed.replace(/\.?0+$/, "");
}

/** "NFO:NIFTY26OCTFUT" -> "NIFTY26OCTFUT". Ids are "EXCHANGE:SYMBOL"; anything else is shown as is. */
export function shortName(instrumentId: string): string {
  const colon = instrumentId.indexOf(":");
  return colon >= 0 ? instrumentId.slice(colon + 1) : instrumentId;
}

const IST_TIME = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Kolkata",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

/** A server time (ISO text) as India time: "10:10:05", or "10:10:05.123" with `ms`
 *  (the audit trail wants milliseconds; the toolbar clock doesn't). Bad input -> "–". */
export function clock(iso: string, ms = true): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "–";
  const hms = IST_TIME.format(date);
  return ms ? `${hms}.${String(date.getUTCMilliseconds()).padStart(3, "0")}` : hms;
}
