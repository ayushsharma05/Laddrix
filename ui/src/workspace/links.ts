// Link groups: widgets with the same colour follow the same instrument. Click a row in a
// pink Watch list and every pink ladder and ticket switches to that instrument.

export type LinkGroup = "none" | "pink" | "amber" | "blue" | "green";

export const LINK_GROUPS: LinkGroup[] = ["none", "pink", "amber", "blue", "green"];

export const LINK_COLOURS: Record<LinkGroup, string> = {
  none: "#4A5566",
  pink: "#D63FD6",
  amber: "#E0B42E",
  blue: "#3D86F0",
  green: "#2EB67D",
};

export function nextLink(group: LinkGroup): LinkGroup {
  const i = LINK_GROUPS.indexOf(group);
  return LINK_GROUPS[(i + 1) % LINK_GROUPS.length]!;
}

/** A price picked on one widget (a chart) for the group's ticket to use. */
export interface PricePick {
  instrumentId: string;
  pricePaise: number;
  seq: number; // each pick once
  at: number; // when (ms): a ticket opened much later doesn't take an old pick
}

export const PICK_FRESH_MS = 5000;

export class LinkBus {
  private listeners = new Map<LinkGroup, Set<(instrumentId: string) => void>>();
  private current = new Map<LinkGroup, string>();
  private priceListeners = new Map<LinkGroup, Set<(pick: PricePick) => void>>();
  private picks = new Map<LinkGroup, PricePick>();
  private seq = 0;

  /** Tell every widget in `group` to show `instrumentId`. "none" links nothing. */
  publish(group: LinkGroup, instrumentId: string): void {
    if (group === "none") return;
    this.current.set(group, instrumentId);
    for (const listener of this.listeners.get(group) ?? []) listener(instrumentId);
  }

  /** The instrument a group last switched to (a new widget in the group starts there). */
  instrumentOf(group: LinkGroup): string | undefined {
    return group === "none" ? undefined : this.current.get(group);
  }

  /** Does any widget follow this group? */
  hasFollowers(group: LinkGroup): boolean {
    return (this.listeners.get(group)?.size ?? 0) > 0;
  }

  subscribe(group: LinkGroup, listener: (instrumentId: string) => void): () => void {
    if (group === "none") return () => {};
    let set = this.listeners.get(group);
    if (!set) this.listeners.set(group, (set = new Set()));
    set.add(listener);
    return () => set!.delete(listener);
  }

  /** Offer a price to the group (a chart click): its ticket on that instrument takes it. */
  pickPrice(group: LinkGroup, instrumentId: string, pricePaise: number, now = Date.now()): void {
    if (group === "none") return;
    const pick = { instrumentId, pricePaise, seq: ++this.seq, at: now };
    this.picks.set(group, pick);
    for (const listener of this.priceListeners.get(group) ?? []) listener(pick);
  }

  /** The group's newest pick if it is still fresh (a ticket that switched instrument for it). */
  freshPick(group: LinkGroup, now = Date.now()): PricePick | undefined {
    const pick = this.picks.get(group);
    return pick && now - pick.at <= PICK_FRESH_MS ? pick : undefined;
  }

  subscribePrice(group: LinkGroup, listener: (pick: PricePick) => void): () => void {
    if (group === "none") return () => {};
    let set = this.priceListeners.get(group);
    if (!set) this.priceListeners.set(group, (set = new Set()));
    set.add(listener);
    return () => set!.delete(listener);
  }
}
