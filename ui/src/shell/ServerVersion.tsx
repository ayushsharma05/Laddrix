// The running server's version in the status bar (V1.6, #289), as the server says it
// (GET /api/health): after a deploy or a rollback, everyone can see which version they're on.
import { useEffect, useState } from "react";
import { useApp } from "../app/context";

interface Health {
  version?: string;
  build?: string;
}

export function ServerVersion() {
  const { rest } = useApp();
  const [health, setHealth] = useState<Health | null>(null);
  useEffect(() => {
    let live = true;
    rest
      .get<Health>("/api/health")
      .then((h) => live && setHealth(h))
      .catch(() => undefined); // unknown: show nothing rather than a guess
    return () => {
      live = false;
    };
  }, [rest]);
  if (!health?.version) return null;
  return (
    <span className="page-status server-version" title={`Laddrix ${health.version}, build ${health.build ?? "unknown"}`}>
      v{health.version}
    </span>
  );
}
