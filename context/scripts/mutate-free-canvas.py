"""#410 mutation check: break one rule of free pages (snapping, the grid, Alt, placing a new
window, the canvas growing past the screen, z order, validation of saved windows, the
controller's free/docked handling, the lock, the frame's drag and keys); the UI tests must
notice. Restores each file in `finally`; a hang counts as caught. Run alone from the repo root."""
import subprocess

C = "ui/src/workspace/canvas.ts"
L = "ui/src/workspace/layouts.ts"
K = "ui/src/workspace/controller.ts"
F = "ui/src/workspace/FreeCanvas.tsx"
TESTS = ["npx", "vitest", "run", "src/workspace"]
MUTANTS = [
 # canvas.ts: the rules of the canvas
 (C, "    if (d <= SNAP && (best === null", "    if (d < 0 && (best === null"),
 (C, "  return [0, ...others.flatMap((o) => [o[axis], o[axis] + o[size] + GAP])];", "  return [0, ...others.flatMap((o) => [o[axis], o[axis] + o[size]])];"),
 (C, "  return others.flatMap((o) => [o[axis] + o[size], o[axis] - GAP]);", "  return others.flatMap((o) => [o[axis] + o[size]]);"),
 (C, "  if (free) return tidy(box);\n", ""),
 (C, "    return toGrid(box[axis]);", "    return box[axis];"),
 (C, "Math.abs(lead - box[axis]) <= Math.abs(trail - size - box[axis]))) return lead;", "false)) return lead;"),
 (C, "  if (edges.left) x = clamp(stick(start.x + dx, leadTargets(others, \"x\")), 0, right - MIN_W);", "  if (edges.left) x = stick(start.x + dx, leadTargets(others, \"x\"));"),
 (C, "  if (edges.right) right = stick(right + dx, trailTargets(others, \"x\"));", "  if (edges.right) right = right + dx;"),
 (C, "  if (edges.bottom) bottom = stick(bottom + dy, trailTargets(others, \"y\"));", "  if (edges.bottom) bottom = bottom + dy;"),
 (C, "  return tidy({ x, y, w: right - x, h: bottom - y }); // tidy", "  return { x, y, w: right - x, h: bottom - y }; // tidy"),
 (C, "  const stick = (edge: number, targets: number[]) => (free ? Math.round(edge) :", "  const stick = (edge: number, targets: number[]) => (false ? Math.round(edge) :"),
 (C, "    tries.push(...(direction === \"right\" ? [right, below] : [below, right]));", "    tries.push(right, below);"),
 (C, "  for (const b of tries) if (free(b) && overlaps(b, view)) return tidy(b);", "  for (const b of tries) if (free(b)) return tidy(b);"),
 (C, "  while (windows.some((o) => o.x === b.x && o.y === b.y)) b =", "  while (false) b ="),
 (C, "    w = Math.max(w, o.x + o.w + ROOM);", "    w = Math.max(w, o.x + o.w);"),
 (C, "    h = Math.max(h, o.y + o.h + ROOM);", ""),
 (C, "  if (!target || (target.z === top && windows.filter((w) => w.z === top).length === 1)) return windows;", "  if (!target) return windows;"),
 (C, "  z.set(id, order.length + 1);", "  z.set(id, 1);"),
 (C, "  return { x: clamp(Math.round(box.x), 0, MAX_POS), y:", "  return { x: Math.round(box.x), y:"),
 (C, "  const w = clamp(Math.round(box.w), MIN_W, MAX_SIDE);", "  const w = Math.round(box.w);"),
 (C, "  return new Set(windows.map((w) => w.id)).size === windows.length;", "  return true;"),
 (C, "    [w.x, w.y, w.w, w.h, w.z].every(finite)", "    [w.x, w.y, w.w, w.h, w.z].every((n) => typeof n === \"number\")"),
 (C, "    !Array.isArray(w.params) &&", ""),
 (C, "  if (!Array.isArray(windows) || windows.length > MAX_WINDOWS ||", "  if (!Array.isArray(windows) ||"),
 # layouts.ts: what a saved page may be
 (L, "  return page.mode === \"free\" ? \"free\" : \"docked\";", "  return page.mode === \"docked\" ? \"docked\" : \"free\";"),
 (L, "  if (p.mode !== undefined && p.mode !== \"free\" && p.mode !== \"docked\") return false;", ""),
 (L, "  return p.canvas === undefined || p.canvas === null || isCanvas(p.canvas);", "  return true;"),
 # controller.ts: free pages in the workspace
 (K, "export function freshDoc(mode: PageMode = \"free\"): WorkspaceDoc {\n  return { version: 1, pages: [{ name: \"Main\", mode,", "export function freshDoc(mode: PageMode = \"free\"): WorkspaceDoc {\n  return { version: 1, pages: [{ name: \"Main\", mode: \"docked\","),
 (K, "    return this.opts.newPageMode ?? \"free\";", "    return \"docked\";"),
 (K, "      if (modeOf(p) === \"free\") return { name: p.name, mode: \"free\" as const, dock: null, canvas: this.canvas };", "      if (modeOf(p) === \"free\") return p;"),
 (K, "        this.canvas = { windows: page.canvas ? page.canvas.windows : await this.defaultWindows(page.name) };", "        this.canvas = { windows: await this.defaultWindows(page.name) };"),
 (K, "      this.closePopouts();\n      this.canvas = null;\n      this.reveal = null;", "      this.canvas = null;\n      this.reveal = null;"),
 (K, "      const front = panel.group?.activePanel?.id === panel.id ? panels.length : 0;", "      const front = 0;"),
 (K, "          ? tidy({ x: r.left - left + n * 24, y: r.top - top + n * 24, w: r.width, h: r.height })", "          ? tidy({ x: r.left + n * 24, y: r.top + n * 24, w: r.width, h: r.height })"),
 (K, "          : placeNew(out, size, this.viewport());", "          : { x: 0, y: 0, w: size.w, h: size.h };"),
 (K, "      const newRow = prev === null || w.y >= (rowTop + rowBottom) / 2;", "      const newRow = prev === null;"),
 (K, '      const windows = this.canvas.windows;\n      if (windows.length >= MAX_WINDOWS) {', '      const windows = this.canvas.windows;\n      if (windows.length > MAX_WINDOWS) {'),
 (K, '        : placeNew(windows, size, this.viewport(), from, def.link === "follows" ? "right" : "below");', '        : placeNew(windows, size, this.viewport(), from, "right");'),
 (K, "      this.reveal = added.id;", ""),
 (K, "    if (!this.canvas || this.locked) return false;\n    const next = tidy(box);", "    if (!this.canvas) return false;\n    const next = tidy(box);"),
 (K, "      if (w.id !== id || (w.x === next.x && w.y === next.y && w.w === next.w && w.h === next.h)) return w;", "      if (w.id !== id) return w;"),
 (K, "    if (windows !== this.canvas.windows) this.setWindows(windows);", ""),
 (K, "    if (!this.canvas || this.locked) return;\n    if (this.popouts.has(id))", "    if (!this.canvas) return;\n    if (this.popouts.has(id))"),
 (K, "    this.setWindows(this.canvas.windows.map((w) => (w.id === id ? { ...w, params } : w)));", ""),
 (K, "    const opened = (this.opts.openWindow ?? openPopout)(POPOUT_URL, popoutBoxFor(win, this.viewport()));\n    if (!opened) {", "    const opened = (this.opts.openWindow ?? openPopout)(POPOUT_URL, popoutBoxFor(win, this.viewport()));\n    if (false) {"),
 (K, "    width: Math.max(win.w, want?.width ?? 0, 360),\n    height: Math.max(win.h, want?.height ?? 0, 640),", "    width: win.w,\n    height: win.h,"),
 (K, "        if (!popped.closed) popped.close();", ""),
 (K, "    if (this.locked) {\n      this.opts.notify(\"INFO\", \"The layout is locked: unlock it to change the page\");\n      return;\n    }", ""),
 (K, "        const windows = this.windowsFromDock(api);\n        api.clear();", "        const windows: CanvasWindow[] = [];\n        api.clear();"),
 (K, "        this.dockWindows(api, windows);", ""),
 (K, "    if (this.canvas) return this.popOutWindow(panelId);", ""),
 # FreeCanvas.tsx: the frame
 (F, "      last = edges ? snapResize(from, dx, dy, edges, others, ev.altKey) : snapMove({ ...from, x: from.x + dx, y: from.y + dy }, others, ev.altKey);", "      last = edges ? snapResize(from, dx, dy, edges, others, false) : snapMove({ ...from, x: from.x + dx, y: from.y + dy }, others, false);"),
 (F, "      controller.moveWindow(win.id, last);", ""),
 (F, "    const step = e.altKey ? 1 : GRID;", "    const step = GRID;"),
 (F, "    const next = e.shiftKey ? { ...win, w: win.w + dx, h: win.h + dy } : { ...win, x: win.x + dx, y: win.y + dy };", "    const next = { ...win, x: win.x + dx, y: win.y + dy };"),
 (F, "    if (!d || locked || maxBox) return;", "    if (!d) return;"),
 (F, "      {!locked &&\n        !maxBox &&", "      {!maxBox &&"),
 (F, "      onPointerDownCapture={() => controller.focusWindow(win.id)}", ""),
 (F, "        onDoubleClick={() => toggleMax((current) => (current === win.id ? null : win.id))}", "        onDoubleClick={() => toggleMax(() => win.id)}"),
 (F, "  const size = extent(boxes, view);", "  const size = view;"),
 (F, "        disabled={locked}", ""),
 (F, "          onClick={() => controller.setWindowParams(win.id, { ...win.params, link: nextLink(link) })}", "          onClick={() => undefined}"),
 (F, "      {windows.length === 0 && empty}", ""),
 (F, "  const update = useCallback((next: BaseParams) => controller.setWindowParams(win.id, next as CanvasWindow[\"params\"]), [controller, win.id]);", "  const update = useCallback((_next: BaseParams) => undefined, []);"),
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
