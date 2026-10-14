// Order specs (what can be ordered on an instrument through an account) fetched once per
// account + instrument and shared by every ticket and ladder. See domain/order_spec.py.

import type { Rest } from "../api/rest";
import type { OrderSpec } from "../api/types";

export class OrderSpecs {
  private known = new Map<string, OrderSpec>();
  private inFlight = new Map<string, Promise<OrderSpec | null>>();

  constructor(private readonly rest: Rest) {}

  private key(accountId: string, instrumentId: string): string {
    return `${accountId}|${instrumentId}`;
  }

  peek(accountId: string, instrumentId: string): OrderSpec | undefined {
    return this.known.get(this.key(accountId, instrumentId));
  }

  /** The spec, or null if the server can't give one (unknown instrument, no access, offline). */
  get(accountId: string, instrumentId: string): Promise<OrderSpec | null> {
    const key = this.key(accountId, instrumentId);
    const known = this.known.get(key);
    if (known) return Promise.resolve(known);
    let pending = this.inFlight.get(key);
    if (!pending) {
      pending = this.rest
        .get<OrderSpec>(`/api/accounts/${encodeURIComponent(accountId)}/instruments/${encodeURIComponent(instrumentId)}/order-spec`)
        .then((spec) => {
          this.known.set(key, spec);
          return spec;
        })
        .catch(() => null) // not cached: the next ask tries again
        .finally(() => this.inFlight.delete(key));
      this.inFlight.set(key, pending);
    }
    return pending;
  }
}
