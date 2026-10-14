// Instrument details (tick size, lot size, name) fetched once and shared by every widget.

import type { Rest } from "../api/rest";
import type { Instrument } from "../api/types";

export class Instruments {
  private known = new Map<string, Instrument>();
  private inFlight = new Map<string, Promise<Instrument | null>>();

  constructor(private readonly rest: Rest) {}

  /** Already loaded? (synchronous, for rendering) */
  peek(id: string): Instrument | undefined {
    return this.known.get(id);
  }

  /** Details for one instrument; null if the server doesn't know it. One request per id. */
  get(id: string): Promise<Instrument | null> {
    const known = this.known.get(id);
    if (known) return Promise.resolve(known);
    let pending = this.inFlight.get(id);
    if (!pending) {
      pending = this.rest
        .get<Instrument>(`/api/instruments/${encodeURIComponent(id)}`)
        .then((i) => {
          this.known.set(id, i);
          return i;
        })
        .catch(() => null)
        .finally(() => this.inFlight.delete(id));
      this.inFlight.set(id, pending);
    }
    return pending;
  }

  async search(q: string, limit = 20): Promise<Instrument[]> {
    const found = await this.rest.get<Instrument[]>(
      `/api/instruments?q=${encodeURIComponent(q)}&limit=${limit}`,
    );
    for (const i of found) if (!this.known.has(i.id)) this.known.set(i.id, i);
    return found;
  }
}

