import { navigationAllowed, permissionAllowed, POPOUT_PATH, secureWebPreferences, windowOpenDecision } from "./security";

const ORIGIN = "https://trade.example.in";

describe("the page can't reach the computer", () => {
  it("no Node, no Electron APIs, isolated and sandboxed, every window", () => {
    expect(secureWebPreferences()).toEqual({
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
    });
    expect(secureWebPreferences()).not.toHaveProperty("preload");
  });

  it("only our pop-out page opens as an app window", () => {
    expect(windowOpenDecision(`${ORIGIN}${POPOUT_PATH}`, ORIGIN)).toBe("popout");
    expect(windowOpenDecision(`${ORIGIN}/`, ORIGIN)).toBe("deny");
    expect(windowOpenDecision(`${ORIGIN}/api/me`, ORIGIN)).toBe("deny");
    expect(windowOpenDecision(`https://evil.example.com${POPOUT_PATH}`, ORIGIN)).toBe("external");
    expect(windowOpenDecision("https://zerodha.com/", ORIGIN)).toBe("external"); // the normal browser
  });

  it("refuses dangerous schemes and look-alike hosts", () => {
    for (const url of ["file:///C:/Windows/system32", "javascript:alert(1)", "data:text/html,<b>x</b>", "http://trade.example.in/popout.html", "not a url"]) {
      expect(windowOpenDecision(url, ORIGIN)).toBe("deny");
    }
    expect(navigationAllowed("https://trade.example.in.evil.com/", ORIGIN)).toBe(false);
    expect(navigationAllowed(`${ORIGIN}/workspace`, ORIGIN)).toBe(true);
    expect(navigationAllowed("https://trade.example.in:444/", ORIGIN)).toBe(false);
  });

  it("grants no device permissions", () => {
    for (const p of ["media", "geolocation", "notifications", "clipboard-read", "openExternal"]) expect(permissionAllowed(p)).toBe(false);
  });
});
