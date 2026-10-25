// Which Laddrix server the desktop app opens. The app ships no screens of its own: it shows
// the server's (so the desktop and the browser are always the same version).
//
//   --server=https://trade.example.in   (command line, wins)
//   LADDRIX_URL=https://…             (environment)
//   settings.json {"server": "https://…"} in the app's data folder
//   http://localhost:8000               (default: the demo server on this computer)
//
// Only https is accepted, except for this computer itself (localhost) during development:
// the app holds a session that can place real orders, so it must never talk in clear text.

export const DEFAULT_SERVER = "http://localhost:8000";

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

export class ConfigError extends Error {}

/** The server's origin ("https://trade.example.in"), checked; throws ConfigError with a readable reason. */
export function checkServer(raw: string): string {
  let url: URL;
  try {
    url = new URL(raw.trim());
  } catch {
    throw new ConfigError(`"${raw}" isn't a web address (example: https://trade.example.in)`);
  }
  const local = LOCAL_HOSTS.has(url.hostname);
  if (url.protocol !== "https:" && !(local && url.protocol === "http:")) {
    throw new ConfigError(`${url.origin}: only https is allowed (plain http only for localhost)`);
  }
  if (url.username || url.password) throw new ConfigError("don't put a username or password in the server address");
  return url.origin;
}

export function resolveServer(argv: string[], env: Record<string, string | undefined>, saved: { server?: unknown } | null): string {
  const flag = argv.find((a) => a.startsWith("--server="))?.slice("--server=".length);
  const fromSettings = typeof saved?.server === "string" ? saved.server : undefined;
  return checkServer(flag ?? env.LADDRIX_URL ?? fromSettings ?? DEFAULT_SERVER);
}
