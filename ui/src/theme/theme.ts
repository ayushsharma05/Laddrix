// The screen's theme (K13.5): dark, light or colour-blind safe, or "system" (follow the computer's
// light/dark setting) until the user chooses. The choice is saved on the server for the user
// (PUT /api/me/settings/theme) so it follows them, and kept in this browser too so the sign-in
// page doesn't flash the wrong colours before the server answers. Switching needs no reload:
// it sets <html data-theme="…">, and theme.css has one block of tokens per theme.

import type { Rest } from "../api/rest";

export type ThemeName = "dark" | "light" | "cb";
export type ThemeChoice = ThemeName | "system";

export const THEME_CHOICES: { value: ThemeChoice; label: string }[] = [
  { value: "system", label: "Follow the computer (dark or light)" },
  { value: "dark", label: "Dark" },
  { value: "light", label: "Light" },
  { value: "cb", label: "Colour-blind safe (dark, blue and orange)" },
];

const PATH = "/api/me/settings/theme";
const LOCAL = "mocktrade.theme"; // this browser's copy, for the first paint
const CHOICES = new Set<string>(THEME_CHOICES.map((c) => c.value));

export interface ThemeEnv {
  prefersLight: () => boolean;
  onSystemChange: (listener: () => void) => () => void;
  root: () => HTMLElement;
  storage: () => Storage | null;
}

export const browserEnv: ThemeEnv = {
  prefersLight: () => typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: light)").matches,
  onSystemChange: (listener) => {
    if (typeof matchMedia !== "function") return () => undefined;
    const q = matchMedia("(prefers-color-scheme: light)");
    q.addEventListener("change", listener);
    return () => q.removeEventListener("change", listener);
  },
  root: () => document.documentElement,
  storage: () => {
    try {
      return window.localStorage;
    } catch {
      return null; // blocked (private window): the server copy still counts
    }
  },
};

/** What a choice shows now. */
export function resolve(choice: ThemeChoice, prefersLight: boolean): ThemeName {
  return choice === "system" ? (prefersLight ? "light" : "dark") : choice;
}

export class ThemeSetting {
  private choice: ThemeChoice = "system";
  private listeners = new Set<() => void>();

  constructor(
    private readonly rest: Rest,
    private readonly env: ThemeEnv = browserEnv,
  ) {
    let saved: string | null = null;
    try {
      saved = env.storage()?.getItem(LOCAL) ?? null;
    } catch {
      saved = null;
    }
    if (saved && CHOICES.has(saved)) this.choice = saved as ThemeChoice;
    env.onSystemChange(() => this.apply()); // "system" follows the computer as it changes
    this.apply();
  }

  getChoice = (): ThemeChoice => this.choice;
  getTheme = (): ThemeName => resolve(this.choice, this.env.prefersLight());

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  /** After sign-in: the user's saved choice (none saved: keep following the computer). */
  async load(): Promise<void> {
    try {
      const answer = await this.rest.get<{ value: { name?: unknown } | null }>(PATH);
      const name = answer.value?.name;
      this.set(typeof name === "string" && CHOICES.has(name) ? (name as ThemeChoice) : "system");
    } catch {
      // the server can't say: keep what this browser showed last
    }
  }

  /** The user picks a theme: shown at once, saved for them (on a refusal, the old one returns). */
  async choose(choice: ThemeChoice): Promise<void> {
    const before = this.choice;
    this.set(choice);
    try {
      await this.rest.put(PATH, { value: choice === "system" ? null : { name: choice } });
    } catch (err) {
      this.set(before);
      throw err;
    }
  }

  private set(choice: ThemeChoice): void {
    this.choice = choice;
    try {
      this.env.storage()?.setItem(LOCAL, choice);
    } catch {
      // storage full or blocked: only this browser's first paint is affected
    }
    this.apply();
  }

  private apply(): void {
    this.env.root().dataset.theme = this.getTheme();
    for (const l of [...this.listeners]) l();
  }
}
