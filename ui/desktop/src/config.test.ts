import { checkServer, ConfigError, DEFAULT_SERVER, resolveServer } from "./config";

describe("which server the app opens", () => {
  it("command line wins, then the environment, then settings, then the local demo", () => {
    const env = { MOCKTRADE_URL: "https://env.example.in" };
    const saved = { server: "https://saved.example.in" };
    expect(resolveServer(["--server=https://flag.example.in"], env, saved)).toBe("https://flag.example.in");
    expect(resolveServer([], env, saved)).toBe("https://env.example.in");
    expect(resolveServer([], {}, saved)).toBe("https://saved.example.in");
    expect(resolveServer([], {}, null)).toBe(DEFAULT_SERVER);
    expect(resolveServer([], {}, { server: 42 })).toBe(DEFAULT_SERVER); // a damaged settings file
  });

  it("keeps only the origin", () => {
    expect(checkServer(" https://trade.example.in/workspace?x=1 ")).toBe("https://trade.example.in");
    expect(checkServer("https://trade.example.in:8443")).toBe("https://trade.example.in:8443");
  });

  it("refuses plain http except on this computer", () => {
    expect(checkServer("http://localhost:8000")).toBe("http://localhost:8000");
    expect(checkServer("http://127.0.0.1:8000")).toBe("http://127.0.0.1:8000");
    expect(() => checkServer("http://trade.example.in")).toThrow(/only https/);
    expect(() => checkServer("file:///etc/passwd")).toThrow(ConfigError);
    expect(() => checkServer("javascript:alert(1)")).toThrow(ConfigError);
  });

  it("refuses nonsense and credentials in the address, with a readable reason", () => {
    expect(() => checkServer("trade")).toThrow(/isn't a web address/);
    expect(() => checkServer("https://me:pw@trade.example.in")).toThrow(/username or password/);
  });
});
