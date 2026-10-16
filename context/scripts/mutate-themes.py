"""#285 mutation check: break one rule of the themes (what "system" shows, following the computer,
the browser copy for the first paint, loading and saving the user's choice, putting the old theme
back on a refusal, the picker, the colours themselves: contrast, the colour-blind hues, a colour
written outside theme.css); the tests must notice. Restores each file in `finally`; a hang counts
as caught. Run alone. Every anchor is checked to be unique."""
import subprocess

T = "ui/src/theme/theme.ts"
P = "ui/src/shell/ThemePick.tsx"
C = "ui/src/styles/theme.css"
W = "ui/src/styles/widgets.css"
UI = (["npx", "vitest", "run", "src/theme", "src/shell/ThemePick", "src/styles"], "ui")
MUTANTS = [
 (T, '  return choice === "system" ? (prefersLight ? "light" : "dark") : choice;', '  return choice === "system" ? "dark" : choice;'),
 (T, "    env.onSystemChange(() => this.apply());", "    env.onSystemChange(() => undefined);"),
 (T, "    if (saved && CHOICES.has(saved)) this.choice = saved as ThemeChoice;", "    if (saved) this.choice = saved as ThemeChoice;"),
 (T, "    if (saved && CHOICES.has(saved)) this.choice = saved as ThemeChoice;\n", "\n"),
 (T, '      this.set(typeof name === "string" && CHOICES.has(name) ? (name as ThemeChoice) : "system");', '      this.set((name as ThemeChoice) ?? "system");'),
 (T, '      // the server can\'t say: keep what this browser showed last', '      this.set("system");'),
 (T, '      await this.rest.put(PATH, { value: choice === "system" ? null : { name: choice } });', '      await this.rest.put(PATH, { value: { name: choice } });'),
 (T, "      this.set(before);\n      throw err;", "      throw err;"),
 (T, "      this.env.storage()?.setItem(LOCAL, choice);", "      this.env.storage()?.setItem(LOCAL, \"dark\");"),
 (T, "    try {\n      this.env.storage()?.setItem(LOCAL, choice);\n    } catch {\n      // storage full or blocked: only this browser's first paint is affected\n    }", "    this.env.storage()?.setItem(LOCAL, choice);"),
 (T, "    for (const l of [...this.listeners]) l();", ""),
 (P, "          setProblem(null);\n", ""),
 (P, "      {problem && (", "      {false && ("),
 (C, "  --last: #7b4d93; /*", "  --last: #2a6f9e; /*"),
 (C, "  --ask: #a8510b;", "  --ask: #b92e38;"),
 (C, "  --ladder-ink: #18202b;", "  --ladder-ink: #ffffff;"),
 (C, "  --bid: #1e61bb;", "  --bid: #3b82f6;"),
 (W, "  color: var(--ladder-ink);", "  color: #ffffff;"),
]
caught = 0
for f, a, b in MUTANTS:
    src = open(f).read()
    if src.count(a) != 1:
        print("NOT UNIQUE OR MISSING:", f, a[:70]); continue
    cmd, cwd = UI
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=300, cwd=cwd)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
