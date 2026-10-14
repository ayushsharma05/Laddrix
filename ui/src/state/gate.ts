// "Nothing is sent blind" (docs/design/ui.md §1): before any order click, why it can't go.
// The server checks again (risk, kill switches, permissions); this is so the screen can say
// why at once and never sends while it can't see what happens next.

import type { State } from "./store";

/** The reason trading is blocked right now, or null if order clicks may be sent. */
export function tradingBlock(s: State): string | null {
  if (!s.me) return "not logged in";
  if (s.stream !== "open") return "disconnected from MockTrade: order clicks are paused";
  const account = s.me.accounts.find((a) => a.id === s.accountId);
  if (!account) return "no account selected";
  if (!account.can_trade) return `you can view ${account.id} but not trade it`;
  if (!account.running) return `${account.id} isn't running on the server`;
  if (s.firmKill.engaged) return `all trading is stopped${s.firmKill.reason ? `: ${s.firmKill.reason}` : ""}`;
  const kill = s.books[account.id]?.kill;
  if (kill?.engaged) return `trading is stopped on ${account.id}${kill.reason ? `: ${kill.reason}` : ""}`;
  return null;
}
