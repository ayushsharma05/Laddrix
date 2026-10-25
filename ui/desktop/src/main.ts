// The Laddrix desktop app (Electron main process).
//
//   app ready ─► which server (config.ts) ─► one main window on our server's page
//                 every window: secure settings (security.ts), no Node in pages, no preload
//   window.open("/popout.html") from the page ─► a real app window for a popped-out widget
//                 (the page draws the widget into it: same live connection, same state)
//   anything else: other sites open in the normal browser; file:, data:… are refused
//   the main window's place is remembered and restored on screens that still exist
//
// The rules live in small tested modules; this file only connects them to Electron.

import { app, BrowserWindow, dialog, screen, session, shell, type WebContents } from "electron";
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { ConfigError, resolveServer } from "./config";
import { navigationAllowed, permissionAllowed, secureWebPreferences, windowOpenDecision } from "./security";
import { placeWindow, type WindowState } from "./windowState";

const settingsFile = () => join(app.getPath("userData"), "settings.json");
const stateFile = () => join(app.getPath("userData"), "window.json");

function readJson<T>(file: string): T | null {
  try {
    return JSON.parse(readFileSync(file, "utf8")) as T;
  } catch {
    return null; // missing or damaged: use the defaults
  }
}

/** Every window's page: stay on our server, open only our pop-out page, no device access. */
function lockDown(contents: WebContents, origin: string): void {
  contents.setWindowOpenHandler(({ url }) => {
    const decision = windowOpenDecision(url, origin);
    if (decision === "popout") {
      return {
        action: "allow",
        overrideBrowserWindowOptions: {
          title: "Laddrix",
          backgroundColor: "#14181d",
          autoHideMenuBar: true,
          webPreferences: secureWebPreferences(),
        },
      };
    }
    if (decision === "external") void shell.openExternal(url);
    return { action: "deny" };
  });
  contents.on("will-navigate", (event, url) => {
    if (!navigationAllowed(url, origin)) event.preventDefault();
  });
  contents.on("will-attach-webview", (event) => event.preventDefault());
}

function createMainWindow(origin: string): BrowserWindow {
  const display = screen.getPrimaryDisplay();
  const place = placeWindow(
    readJson<WindowState>(stateFile()),
    screen.getAllDisplays().map((d) => d.workArea),
    display.workArea,
  );
  const win = new BrowserWindow({
    x: place.x,
    y: place.y,
    width: place.width,
    height: place.height,
    minWidth: 800,
    minHeight: 500,
    title: "Laddrix",
    backgroundColor: "#14181d",
    autoHideMenuBar: true,
    show: false,
    webPreferences: secureWebPreferences(),
  });
  if (place.maximized) win.maximize();
  win.once("ready-to-show", () => win.show());
  const remember = () => {
    if (win.isDestroyed()) return;
    const bounds = win.getNormalBounds();
    try {
      writeFileSync(stateFile(), JSON.stringify({ ...bounds, maximized: win.isMaximized() }));
    } catch {
      // a nicety: never stop the app over it
    }
  };
  win.on("close", remember);
  void win.loadURL(origin);
  return win;
}

async function start(): Promise<void> {
  let origin: string;
  try {
    origin = resolveServer(process.argv, process.env, readJson<{ server?: unknown }>(settingsFile()));
  } catch (err) {
    const reason = err instanceof ConfigError ? err.message : String(err);
    dialog.showErrorBox("Laddrix can't start", `${reason}\n\nSet the server in ${settingsFile()}`);
    app.quit();
    return;
  }
  session.defaultSession.setPermissionRequestHandler((_wc, permission, callback) => callback(permissionAllowed(permission)));
  session.defaultSession.setPermissionCheckHandler((_wc, permission) => permissionAllowed(permission));
  app.on("web-contents-created", (_event, contents) => lockDown(contents, origin));
  const main = createMainWindow(origin);
  app.on("second-instance", () => {
    if (main.isMinimized()) main.restore();
    main.focus();
  });
  if (process.env.LADDRIX_SMOKE) {
    // smoke test: report what the page can reach, then quit (see the desktop README)
    main.webContents.once("did-finish-load", async () => {
      const probe = await main.webContents.executeJavaScript(
        "({ require: typeof require, process: typeof process, electron: typeof window.electron })",
      );
      const metrics = app.getAppMetrics().map((m) => ({ type: m.type, kb: m.memory.workingSetSize }));
      console.log(`LADDRIX_SMOKE ${JSON.stringify({ origin, probe, metrics })}`);
      app.quit();
    });
  }
}

if (!app.requestSingleInstanceLock()) {
  app.quit(); // already running: the first copy comes to the front instead
} else {
  app.on("window-all-closed", () => app.quit());
  void app.whenReady().then(start);
}
