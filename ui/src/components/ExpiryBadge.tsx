// Expiry warnings (#290): the server says when a contract ends and how it settles; this only
// shows it. A small tag before the name where space is short (positions, watch list), a line
// of words above where there is room (the ticket, the ladder, the option chain).
//
//   expires_today ─► "EXP TODAY" tag (amber), the server's sentence on hover
//   expired       ─► "EXPIRED" tag (red)
//   otherwise     ─► nothing

import type { ExpiryInfo } from "../api/types";

export function expiryTag(info: ExpiryInfo | null | undefined): string | null {
  if (!info) return null;
  if (info.expired) return "EXPIRED";
  if (info.expires_today) return "EXP TODAY";
  return null;
}

/** `compact`: "EXP" / "ENDED", for columns too narrow for the name and "EXP TODAY". */
export function ExpiryBadge({ info, compact = false }: { info: ExpiryInfo | null | undefined; compact?: boolean }) {
  const tag = expiryTag(info);
  if (!tag || !info) return null;
  const text = compact ? (info.expired ? "ENDED" : "EXP") : tag;
  return (
    <span className={`expiry-tag${info.expired ? " ended" : ""}`} title={info.note} aria-label={tag}>
      {text}
    </span>
  );
}

/** The server's sentence, as a warning line, on the day (or once it has ended). */
export function ExpiryNote({ info }: { info: ExpiryInfo | null | undefined }) {
  if (!info || !expiryTag(info)) return null;
  return (
    <div className={`expiry-note${info.expired ? " ended" : ""}`} role="note">
      {info.note}
    </div>
  );
}
