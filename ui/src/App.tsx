// The page: checks the session, then shows either the sign-in or the terminal.

import { useEffect, useState } from "react";
import { useApp, useStore } from "./app/context";
import { Login } from "./shell/Login";
import { Shell } from "./shell/Shell";

export function App() {
  const { session } = useApp();
  const me = useStore((s) => s.me);
  const [booted, setBooted] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    const tryBoot = () => {
      session.boot().then(
        () => live && setBooted(true),
        (err: Error) => {
          if (!live) return;
          setError(err.message);
          setTimeout(tryBoot, 3000); // the server may be starting: keep trying
        },
      );
    };
    tryBoot();
    return () => {
      live = false;
    };
  }, [session]);

  if (!booted) {
    return (
      <div className="splash" role="status">
        {error ? `Waiting for the MockTrade server… (${error})` : "Loading MockTrade…"}
      </div>
    );
  }
  return me ? <Shell /> : <Login />;
}
