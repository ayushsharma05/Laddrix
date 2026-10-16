// Keyboard shortcuts: every action a key can start, by where it works, with its keys (K13.4;
// docs/design/ui.md). Click "+ key", press the key: it is checked (the browser's own keys and
// clashes are refused, said in words) and saved on the server, so it follows you. STOP always
// keeps Ctrl+Shift+K, whatever else it gets.
//
//   Anywhere
//     STOP trading (opens the stop dialog)   [Ctrl+Shift+K 🔒] [F9 ×]   + key
//     Save the layout                        [Ctrl+S ×]                 + key   default

import { useState } from "react";
import { useApp, useKeyBindings } from "../../app/context";
import { ACTIONS, SCOPES, chordOf, keysOf, refusal, withKey, withoutKey, type Bindings } from "../../keys/model";
import { registerWidget, type BaseParams, type WidgetProps } from "../../workspace/registry";

const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function KeysEditor(_props: WidgetProps<BaseParams>) {
  const { keys } = useApp();
  const bindings = useKeyBindings();
  const [capturing, setCapturing] = useState<string | null>(null); // the action waiting for a key
  const [problem, setProblem] = useState<string | null>(null);
  const [armed, setArmed] = useState(false); // "reset all" waits for a second click

  const save = (next: Bindings) => {
    setProblem(null);
    keys.save(next).catch((e: unknown) => setProblem(`Not saved: ${errorText(e)}`));
  };
  const take = (actionId: string, e: React.KeyboardEvent) => {
    e.preventDefault();
    e.stopPropagation(); // the key is being chosen, not used
    if (e.key === "Escape") {
      setCapturing(null);
      return;
    }
    const chord = chordOf(e);
    if (!chord) return; // a modifier on its own: wait for the key
    const why = refusal(actionId, chord, bindings);
    if (why) {
      setProblem(why);
      return;
    }
    setCapturing(null);
    save(withKey(bindings, actionId, chord));
  };
  const reset = (actionId: string) => {
    const next = { ...bindings };
    delete next[actionId];
    save(next);
  };

  return (
    <div className="book keys-editor">
      <div className="book-bar">
        <span className="dim">Click “+ key”, then press the key. Esc cancels. Saved for you on the server.</span>
        <span className="book-spacer" />
        {Object.keys(bindings).length > 0 &&
          (armed ? (
            <button type="button" className="btn small danger" onClick={() => (setArmed(false), save({}))}>
              Confirm: back to the defaults
            </button>
          ) : (
            <button type="button" className="btn small" onClick={() => setArmed(true)}>
              All back to the defaults…
            </button>
          ))}
      </div>
      {problem && (
        <div className="empty-note tone-bad" role="alert">
          {problem}
        </div>
      )}
      <div className="book-scroll">
        {SCOPES.map(({ scope, label }) => (
          <section key={scope} className="keys-group" aria-label={label}>
            <h3>{label}</h3>
            {ACTIONS.filter((a) => a.scope === scope).map((a) => {
              const list = keysOf(a, bindings);
              const changed = a.id in bindings;
              return (
                <div key={a.id} className="keys-row" role="group" aria-label={a.label} title={a.help}>
                  <span className="keys-label">{a.label}</span>
                  <span className="keys-list">
                    {!list.length && <span className="dim">no key</span>}
                    {list.map((k) =>
                      k === a.fixed ? (
                        <kbd key={k} className="keys-chip fixed" title="Always works: it can't be removed">
                          {k} 🔒
                        </kbd>
                      ) : (
                        <kbd key={k} className="keys-chip">
                          {k}
                          <button type="button" className="row-action" aria-label={`Remove ${k} from ${a.label}`} onClick={() => save(withoutKey(bindings, a.id, k))}>
                            ×
                          </button>
                        </kbd>
                      ),
                    )}
                    {capturing === a.id ? (
                      <button type="button" className="btn small keys-capture" autoFocus onKeyDown={(e) => take(a.id, e)} onBlur={() => setCapturing(null)}>
                        Press a key…
                      </button>
                    ) : (
                      <button type="button" className="btn small" aria-label={`Add a key to ${a.label}`} onClick={() => (setProblem(null), setCapturing(a.id))}>
                        + key
                      </button>
                    )}
                    {changed && (
                      <button type="button" className="btn small" onClick={() => reset(a.id)} title={`Default: ${a.keys.join(", ") || "no key"}`}>
                        Default
                      </button>
                    )}
                  </span>
                </div>
              );
            })}
          </section>
        ))}
      </div>
    </div>
  );
}

registerWidget<BaseParams>({
  kind: "keys",
  title: "Keyboard shortcuts",
  description: "See and change the keys for each action; saved for you",
  component: KeysEditor,
  defaults: () => ({ link: "none" }),
  link: "none",
  size: { width: 640, height: 520 },
});
