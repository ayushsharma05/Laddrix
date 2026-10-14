// The desktop app's security rules, apart from Electron so they can be tested on their own.
//
// The page is a web page from our server, nothing more: it gets no Node.js, no file system and
// no Electron APIs (no preload script at all). It can only navigate within our server's origin,
// open only our pop-out page as a new window, and gets no device permissions. Anything else is
// refused; links to other sites open in the normal browser, never inside the app.

export interface SecurePreferences {
  contextIsolation: true;
  nodeIntegration: false;
  nodeIntegrationInSubFrames: false;
  nodeIntegrationInWorker: false;
  sandbox: true;
  webSecurity: true;
  allowRunningInsecureContent: false;
  experimentalFeatures: false;
  webviewTag: false;
  spellcheck: false;
}

/** Settings for every window the app opens: main window and pop-outs alike. */
export function secureWebPreferences(): SecurePreferences {
  return {
    contextIsolation: true,
    nodeIntegration: false,
    nodeIntegrationInSubFrames: false,
    nodeIntegrationInWorker: false,
    sandbox: true,
    webSecurity: true,
    allowRunningInsecureContent: false,
    experimentalFeatures: false,
    webviewTag: false,
    spellcheck: false,
  };
}

/** The page a popped-out widget's window loads (ui/public/popout.html). */
export const POPOUT_PATH = "/popout.html";

function parse(url: string): URL | null {
  try {
    return new URL(url);
  } catch {
    return null;
  }
}

export function sameOrigin(url: string, origin: string): boolean {
  return parse(url)?.origin === origin;
}

export type OpenDecision = "popout" | "external" | "deny";

/**
 * What to do with window.open / target=_blank:
 * - our pop-out page → a real app window (a widget on another monitor)
 * - an https page elsewhere → the user's normal browser (never inside the app)
 * - anything else (file:, javascript:, data:, other pages of ours) → refused
 */
export function windowOpenDecision(url: string, origin: string): OpenDecision {
  const u = parse(url);
  if (!u) return "deny";
  if (u.origin === origin) return u.pathname === POPOUT_PATH ? "popout" : "deny";
  return u.protocol === "https:" ? "external" : "deny";
}

/** The main window may only move within our server (login, reloads); never to another site. */
export function navigationAllowed(url: string, origin: string): boolean {
  return sameOrigin(url, origin);
}

/** Camera, microphone, location, notifications…: the trading screens need none of them. */
export function permissionAllowed(_permission: string): boolean {
  return false;
}
