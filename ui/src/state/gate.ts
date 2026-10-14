// "Nothing is sent blind" (docs/design/ui.md §1): before any order click, why it can't go.
// The server checks again (risk, kill switches, permissions); this is so the screen can say
// why at once and never sends while it can't see what happens next.

import type { State } from "./store";

/**
 * The reason cancels can't be sent right now, or null. Cancelling is how you get out, so it
 * stays allowed while trading is stopped: only being unable to see or reach the account blocks it.
 */
export function cancelBlock(s: State, accountId: string | null = s.accountId): string | null {
  if (!s.me) return "not logged in";
  if (s.stream !== "open") return "disconnected from MockTrade: order clicks are paused";
  const account = s.me.accounts.find((a) => a.id === accountId);
  if (!account) return "no account selected";
  if (!account.can_trade) return `you can view ${account.id} but not trade it`;
  if (!account.running) return `${account.id} isn't running on the server`;
  return null;
}

/** The reason new orders (and changes) are blocked on an account right now, or null if they may be sent. */
export function tradingBlock(s: State, accountId: string | null = s.accountId): string | null {
  const blocked = cancelBlock(s, accountId);
  if (blocked) return blocked;
  if (s.firmKill.engaged) return `all trading is stopped${s.firmKill.reason ? `: ${s.firmKill.reason}` : ""}`;
  const kill = accountId ? s.books[accountId]?.kill : undefined;
  if (kill?.engaged) return `trading is stopped on ${accountId}${kill.reason ? `: ${kill.reason}` : ""}`;
  return null;
}
