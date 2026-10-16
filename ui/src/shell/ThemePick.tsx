// The theme picker in the top bar (K13.5): the new colours show at once and are saved for the
// user; if the server refuses, the old theme comes back and the reason is said.
import { useState } from "react";
import { useApp, useThemeChoice } from "../app/context";
import { THEME_CHOICES, type ThemeChoice } from "../theme/theme";

/** The theme picker: shown at once, saved for the user (a refusal is said, the old one returns). */
export function ThemePick() {
  const { theme } = useApp();
  const choice = useThemeChoice();
  const [problem, setProblem] = useState<string | null>(null);
  return (
    <label className="account-pick" title={problem ?? "Screen colours, saved for you"}>
      <span className="dim">Theme</span>
      <select
        value={choice}
        aria-label="Theme"
        aria-invalid={problem ? true : undefined}
        onChange={(e) => {
          setProblem(null);
          theme.choose(e.target.value as ThemeChoice).catch((err: unknown) => setProblem(`Theme not saved: ${err instanceof Error ? err.message : String(err)}`));
        }}
      >
        {THEME_CHOICES.map((c) => (
          <option key={c.value} value={c.value}>
            {c.label}
          </option>
        ))}
      </select>
      {problem && (
        <span className="tone-bad" role="alert">
          {problem}
        </span>
      )}
    </label>
  );
}
