import { ACTIONS, actionFor, chordOf, cleanBindings, keyText, keysOf, refusal, withKey, withoutKey } from "./model";

const press = (key: string, mods: { ctrl?: boolean; meta?: boolean; alt?: boolean; shift?: boolean; code?: string } = {}) =>
  chordOf({ key, code: mods.code ?? "", ctrlKey: !!mods.ctrl, metaKey: !!mods.meta, altKey: !!mods.alt, shiftKey: !!mods.shift });

describe("a key press as one name", () => {
  it("names modifiers and keys the same way every time", () => {
    expect(press("k", { ctrl: true, shift: true, code: "KeyK" })).toBe("Ctrl+Shift+K");
    expect(press("K", { ctrl: true, shift: true, code: "KeyK" })).toBe("Ctrl+Shift+K");
    expect(press("s", { meta: true, code: "KeyS" })).toBe("Ctrl+S"); // ⌘ on a Mac counts as Ctrl
    expect(press("∫", { alt: true, code: "KeyB" })).toBe("Alt+B"); // macOS: Alt types a symbol
    expect(press("1", { alt: true, code: "Digit1" })).toBe("Alt+1");
    expect(press(" ")).toBe("Space");
    expect(press("Esc")).toBe("Escape");
    expect(press("Enter")).toBe("Enter");
    expect(press("ArrowUp", { shift: true })).toBe("Shift+ArrowUp");
    expect(press("/")).toBe("/");
    expect(press("?", { shift: true })).toBe("?"); // typed with Shift: the character says it
    expect(press("b")).toBe("B");
    expect(press("B", { shift: true })).toBe("Shift+B");
    expect(press("Shift", { shift: true })).toBeNull(); // a modifier alone isn't a key yet
    expect(press("Control", { ctrl: true })).toBeNull();
  });
});

describe("which action a key starts", () => {
  it("defaults, the user's choice, scopes, and STOP's fixed key", () => {
    expect(actionFor("Ctrl+Shift+K", "global", {})).toBe("stop");
    expect(actionFor("Enter", "ticket", {})).toBe("ticket-send");
    expect(actionFor("Enter", "ladder", {})).toBeNull(); // the ticket's key, not the ladder's
    expect(actionFor("Escape", "ladder", {})).toBe("ladder-clear");
    expect(actionFor("Ctrl+S", "ticket", {})).toBe("save-layout"); // global keys work in widgets too
    expect(actionFor("Ctrl+S", "global", {})).toBe("save-layout");
    expect(actionFor("Space", "global", {})).toBeNull(); // a ladder key doesn't work anywhere
    const mine = { stop: ["F9"], "ticket-send": ["Ctrl+Enter"] };
    expect(actionFor("F9", "global", mine)).toBe("stop");
    expect(actionFor("Ctrl+Shift+K", "global", mine)).toBe("stop"); // the fixed key stays
    expect(actionFor("Enter", "ticket", mine)).toBeNull(); // replaced
    expect(actionFor("Ctrl+Enter", "ticket", mine)).toBe("ticket-send");
    expect(actionFor(null, "global", {})).toBeNull();
    expect(keysOf(ACTIONS.find((a) => a.id === "stop")!, { stop: [] })).toEqual(["Ctrl+Shift+K"]);
  });
});

describe("changing keys", () => {
  it("refuses the browser's keys and clashes, in words", () => {
    expect(refusal("open-chart", "Ctrl+W", {})).toBe("Ctrl+W belongs to the browser (closing, reloading or moving between tabs and fields)");
    expect(refusal("open-chart", "Tab", {})).toContain("belongs to the browser");
    expect(refusal("open-chart", "Ctrl+S", {})).toBe("Ctrl+S is already “Save the layout”");
    expect(refusal("ladder-centre", "Ctrl+S", {})).toBe("Ctrl+S is already “Save the layout” (anywhere)"); // a widget key can't hide a global one
    expect(refusal("open-chart", "Escape", {})).toBe("Escape is already “Clear the ticket” (order ticket (when it has the focus))"); // and the other way
    expect(refusal("ladder-centre", "Enter", {})).toBeNull(); // different widgets may share a key
    expect(refusal("open-chart", "Ctrl+Shift+K", { stop: [] })).toContain("STOP"); // STOP's fixed key is always taken
    expect(refusal("open-chart", "Alt+C", {})).toBeNull(); // its own key
    expect(refusal("nope", "F2", {})).toBe("no such action");
  });

  it("adds and removes keys; STOP's fixed key can't be removed", () => {
    let b = withKey({}, "open-chart", "F2");
    expect(b["open-chart"]).toEqual(["Alt+C", "F2"]);
    expect(withKey(b, "open-chart", "F2")).toEqual(b); // never twice
    b = withoutKey(b, "open-chart", "Alt+C");
    expect(b["open-chart"]).toEqual(["F2"]);
    expect(withoutKey({}, "stop", "Ctrl+Shift+K")).toEqual({});
    expect(keysOf(ACTIONS.find((a) => a.id === "stop")!, withoutKey(withKey({}, "stop", "F9"), "stop", "Ctrl+Shift+K"))).toEqual(["Ctrl+Shift+K", "F9"]);
  });

  it("keeps only valid saved choices: unknown actions, browser keys and clashes fall back", () => {
    expect(cleanBindings(null)).toEqual({});
    expect(cleanBindings({ gone: ["F1"], "open-chart": ["F2", "F2", 7, "Ctrl+W"] })).toEqual({ "open-chart": ["F2"] });
    expect(cleanBindings({ "open-chart": ["Ctrl+S"] })).toEqual({}); // clashes with save: defaults
    expect(cleanBindings({ "open-chart": "F2" })).toEqual({});
    expect(cleanBindings({ "open-chart": ["Ctrl+W"] })).toEqual({}); // nothing usable: the default, never a dead key
    expect(cleanBindings({ "open-chart": [] })).toEqual({ "open-chart": [] }); // chosen on purpose: no key
  });

  it("the defaults themselves never clash", () => {
    for (const a of ACTIONS) for (const k of a.keys) expect(refusal(a.id, k, {})).toBeNull();
  });
});

describe("keys in hints", () => {
  it("shows the first key the way people read it", () => {
    expect(keyText("ticket-send", {})).toBe("Enter");
    expect(keyText("ticket-clear", {})).toBe("Esc");
    expect(keyText("ticket-up", {})).toBe("↑");
    expect(keyText("ticket-send", { "ticket-send": ["Ctrl+Enter"] })).toBe("Ctrl+Enter");
    expect(keyText("ticket-send", { "ticket-send": [] })).toBe("–");
    expect(keyText("chart-back", { "chart-back": ["Shift+ArrowLeft"] })).toBe("Shift+←");
  });
});
