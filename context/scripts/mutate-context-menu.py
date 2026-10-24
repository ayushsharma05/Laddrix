"""#412 mutation check: break one rule of the right-click menus (the menu component's keys,
clicks, checks and closing; the page menu's items; the widget menu's own settings, contract
widgets, link group, duplicate, pop-out, close; the controller's helpers for free and docked
pages; the lock); the UI tests must notice. Restores each file in `finally`; a hang counts as
caught. Run alone from the repo root."""
import subprocess

M = "ui/src/components/ContextMenu.tsx"
B = "ui/src/workspace/menus.ts"
K = "ui/src/workspace/controller.ts"
TESTS = ["npx", "vitest", "run", "src/components/ContextMenu.test.tsx", "src/workspace"]
MUTANTS = [
 # the menu component
 (M, "              onClose();\n              item.onSelect();", "              item.onSelect();"),
 (M, "              onClose();\n              item.onSelect();", "              onClose();"),
 (M, "    if (e.key === \"ArrowDown\") move(1);", "    if (e.key === \"ArrowDown\") move(0);"),
 (M, "      if (item && isSub(item)) openSub(index, e.target as HTMLElement);", ""),
 (M, "    } else if (e.key === \"ArrowLeft\" && onBack) onBack();", "    } else if (false) onBack!();"),
 (M, "    else if (e.key === \"Escape\") onClose();", ""),
 (M, "      if (root.current && !root.current.contains(e.target as Node)) close.current();", "      close.current();"),
 (M, "    buttons()[0]?.focus();", ""),
 (M, "            <span className=\"cm-check\">{isAction(item) && item.checked ? \"✓\" : \"\"}</span>", "            <span className=\"cm-check\" />"),
 (M, "            aria-checked={isAction(item) && item.checked !== undefined ? item.checked : undefined}", ""),
 (M, "            disabled={item.disabled}", ""),
 (M, "              if (sub && !item.disabled) openSub(i, e.currentTarget);", "              if (false) openSub(i, e.currentTarget);"),
 (M, "    list.current?.querySelector<HTMLButtonElement>(`:scope > [data-index=\"${index}\"]`)?.focus();", ""),
 # the page menu
 (B, "onSelect: () => c.addWidget(w.kind, {}, free ? d.at : undefined) }))", "onSelect: () => c.addWidget(w.kind, {}) }))"),
 (B, "      label: \"Open widget\",\n      disabled: locked,", "      label: \"Open widget\","),
 (B, "        { label: \"Free: any size, anywhere\", checked: free, disabled: locked,", "        { label: \"Free: any size, anywhere\", checked: !free, disabled: locked,"),
 (B, "        { label: \"Docked: tiles that fill the page\", checked: !free, disabled: locked, onSelect: () => c.setPageMode(\"docked\") },", "        { label: \"Docked: tiles that fill the page\", checked: !free, onSelect: () => c.setPageMode(\"free\") },"),
 (B, "        { label: \"Rename…\", onSelect: () => c.requestRename(index) },", "        { label: \"Rename…\", onSelect: () => c.requestRename(0) },"),
 (B, "        { label: \"Close page\", disabled: c.doc.pages.length <= 1,", "        { label: \"Close page\","),
 (B, "        { label: \"Docked\", onSelect: () => void c.addPage(undefined, \"docked\") },", "        { label: \"Docked\", onSelect: () => void c.addPage(undefined) },"),
 (B, "    { label: locked ? \"Unlock layout\" : \"Lock layout\", onSelect: () => c.setLocked(!locked) },", "    { label: locked ? \"Unlock layout\" : \"Lock layout\", onSelect: () => c.setLocked(true) },"),
 (B, "checked: t.value === d.theme,", "checked: false,"),
 (B, "    { label: \"Search product…\", hint: \"/\", onSelect: d.focusSearch },", "    { label: \"Search product…\", hint: \"/\", onSelect: () => {} },"),
 # the widget menu
 (B, "  const own = def?.menu?.(params, { setParams, open }) ?? [];", "  const own: MenuItem[] = [];"),
 (B, "    if (c.canvas) c.focusWindow(id); // a new window goes beside this one", ""),
 (B, "    const others = [...widgets].filter((w) => w.link === \"follows\" && w.kind !== now.kind).sort(byTitle);", "    const others = [...widgets].sort(byTitle);"),
 (B, "onSelect: () => open(w.kind, { instrumentId, link: params.link ?? \"none\" }) })),", "onSelect: () => open(w.kind, { instrumentId }) })),"),
 (B, "  if (def && def.link !== \"none\") {", "  if (def) {"),
 (B, "checked: l === link, onSelect: () => setParams({ link: l }) })),", "checked: l === link, onSelect: () => setParams({}) })),"),
 (B, "    { label: \"Duplicate\", disabled: locked,", "    { label: \"Duplicate\","),
 (B, "    now.popped\n      ? { label", "    false\n      ? { label"),
 (B, "    { label: \"Close\", disabled: locked, onSelect: () => c.closeWidget(id) },", "    { label: \"Close\", onSelect: () => c.closeWidget(id) },"),
 (B, "  if (!now) return null;", "  if (!now) return [];"),
 # the controller's helpers
 (K, "        ? snapMove({ x: at.x, y: at.y, ...size }, [])", "        ? { x: at.x, y: at.y, ...size }"),
 (K, "    const params = { ...now.params, ...change };", "    const params = { ...change } as typeof now.params;"),
 (K, "    this.api?.getPanel(id)?.api.updateParameters(params);", ""),
 (K, "  closeWidget(id: string): void {\n    if (this.locked) return;", "  closeWidget(id: string): void {"),
 (K, "    else this.api?.getPanel(id)?.api.close();", ""),
 (K, "      const box = placeNew(windows, { w: from.w, h: from.h }, this.viewport(), from, \"right\");", "      const box = placeNew(windows, DEFAULT_SIZE, this.viewport(), from, \"right\");"),
 (K, "      position: { referencePanel: id, direction: def.link === \"follows\" ? \"right\" : \"below\" },", ""),
 (K, "    if (this.locked) {\n      this.opts.notify(\"INFO\", \"The layout is locked: unlock it to add widgets\");\n      return;\n    }\n    if (this.canvas) {\n      const windows = this.canvas.windows;\n      const from", "    if (this.canvas) {\n      const windows = this.canvas.windows;\n      const from"),
 (K, "      return w ? { kind: w.kind, params: w.params, popped: this.popouts.has(id) } : null;", "      return w ? { kind: w.kind, params: w.params, popped: false } : null;"),
 (K, "  requestRename(index: number | null): void {\n    this.renaming = index;", "  requestRename(index: number | null): void {"),
]
if subprocess.run(TESTS, capture_output=True, timeout=300, cwd="ui").returncode != 0:
    raise SystemExit("the tests fail (or a test file is missing) before any mutation: fix that first")
caught = 0
for f, a, b in MUTANTS:
    src = open(f).read()
    if src.count(a) != 1:
        print("NOT UNIQUE OR MISSING:", f, a[:70]); continue
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(TESTS, capture_output=True, timeout=300, cwd="ui")
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90].replace(chr(10), " "), "->", b[:40])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
