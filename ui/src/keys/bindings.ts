// The user's keyboard shortcuts, kept on the server (GET/PUT /api/me/settings/keys) so they
// follow the user to another machine. Until they load, and if they can't, the defaults apply:
// a missing setting never leaves a key dead.

import type { Rest } from "../api/rest";
import { cleanBindings, type Bindings } from "./model";

const PATH = "/api/me/settings/keys";

export class KeyBindings {
  private current: Bindings = {};
  private listeners = new Set<() => void>();

  constructor(private readonly rest: Rest) {}

  get = (): Bindings => this.current;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  /** After sign-in: the saved choices, cleaned (anything no longer valid falls back). */
  async load(): Promise<void> {
    try {
      const answer = await this.rest.get<{ value: { bindings?: unknown } | null }>(PATH);
      this.set(cleanBindings(answer.value?.bindings));
    } catch {
      this.set({}); // nothing saved, or the server can't say: the defaults
    }
  }

  /** Save new choices; on a refusal the old ones stay and the error goes to the caller. */
  async save(next: Bindings): Promise<void> {
    const before = this.current;
    this.set(next);
    try {
      await this.rest.put(PATH, { value: Object.keys(next).length ? { bindings: next } : null });
    } catch (err) {
      this.set(before);
      throw err;
    }
  }

  private set(next: Bindings): void {
    this.current = next;
    for (const l of [...this.listeners]) l();
  }
}
