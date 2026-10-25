import { fakeRest } from "../test/fakes";
import { CURRENT, isWorkspaceDoc, LayoutStore, modeOf, type StorageLike, type WorkspaceDoc } from "./layouts";

class MemoryStorage implements StorageLike {
  data = new Map<string, string>();
  getItem(k: string) {
    return this.data.get(k) ?? null;
  }
  setItem(k: string, v: string) {
    this.data.set(k, v);
  }
  removeItem(k: string) {
    this.data.delete(k);
  }
}

const DOC: WorkspaceDoc = {
  version: 1,
  pages: [
    { name: "Main", dock: { grid: { root: "…" }, panels: { "watch-1": { params: { items: ["NFO:X"] } } } } },
    { name: "Options", dock: null },
  ],
  active: 1,
};

/** A server that really keeps what is PUT, like /api/layouts. */
function layoutServer() {
  const saved = new Map<string, unknown>();
  let down = false;
  const server = fakeRest({});
  const handler = (name: string) => (body: unknown, method: string) => {
    if (down) return { status: 0 };
    if (method === "PUT") {
      saved.set(name, (body as { layout: unknown }).layout);
      return { status: 200, body: { name } };
    }
    if (method === "DELETE") return saved.delete(name) ? { status: 204 } : { status: 404, body: { detail: "no such layout" } };
    return saved.has(name) ? { status: 200, body: { name, layout: saved.get(name) } } : { status: 404, body: { detail: "no such layout" } };
  };
  for (const name of [CURRENT, "Morning"]) {
    for (const m of ["GET", "PUT", "DELETE"]) server.routes[`${m} /api/layouts/${name}`] = handler(name);
  }
  server.routes["GET /api/layouts"] = () => ({ status: 200, body: [...saved.keys()].map((name) => ({ name, updated_at: "" })) });
  return { ...server, saved, setDown: (d: boolean) => (down = d) };
}

describe("LayoutStore", () => {
  it("round-trips a workspace through the server", async () => {
    const server = layoutServer();
    const store = new LayoutStore(server.rest, new MemoryStorage(), "ayush");
    await expect(store.save(CURRENT, DOC)).resolves.toBe("server");
    // a different browser: nothing in its storage, the server has it
    const elsewhere = new LayoutStore(server.rest, new MemoryStorage(), "ayush");
    await expect(elsewhere.load(CURRENT)).resolves.toEqual(DOC);
  });

  it("falls back to the browser's copy when the server is down", async () => {
    const server = layoutServer();
    const storage = new MemoryStorage();
    const store = new LayoutStore(server.rest, storage, "ayush");
    server.setDown(true);
    await expect(store.save(CURRENT, DOC)).resolves.toBe("browser");
    await expect(store.load(CURRENT)).resolves.toEqual(DOC);
  });

  it("keeps each user's browser copy separate", async () => {
    const server = layoutServer();
    server.setDown(true);
    const storage = new MemoryStorage();
    await new LayoutStore(server.rest, storage, "ayush").save(CURRENT, DOC);
    await expect(new LayoutStore(server.rest, storage, "priya").load(CURRENT)).resolves.toBeNull();
  });

  it("ignores damaged layouts instead of crashing", async () => {
    const server = layoutServer();
    const storage = new MemoryStorage();
    server.saved.set(CURRENT, { version: 2, pages: [] });
    storage.setItem("laddrix.layout.ayush.current", "{not json");
    await expect(new LayoutStore(server.rest, storage, "ayush").load(CURRENT)).resolves.toBeNull();
  });

  it("works without browser storage at all", async () => {
    const server = layoutServer();
    const store = new LayoutStore(server.rest, null, "ayush");
    await store.save("Morning", DOC);
    await expect(store.load("Morning")).resolves.toEqual(DOC);
  });

  it("lists named layouts without the autosave, and deletes", async () => {
    const server = layoutServer();
    const store = new LayoutStore(server.rest, new MemoryStorage(), "ayush");
    await store.save(CURRENT, DOC);
    await store.save("Morning", DOC);
    await expect(store.list()).resolves.toEqual(["Morning"]);
    await store.remove("Morning");
    await expect(store.list()).resolves.toEqual([]);
  });

  it("passes on a refusal from the server (too big, too many)", async () => {
    const server = layoutServer();
    server.routes[`PUT /api/layouts/${CURRENT}`] = () => ({ status: 422, body: { detail: "layout is too big" } });
    const store = new LayoutStore(server.rest, new MemoryStorage(), "ayush");
    await expect(store.save(CURRENT, DOC)).rejects.toThrow("layout is too big");
  });
});

describe("isWorkspaceDoc", () => {
  it("accepts only well-formed documents", () => {
    expect(isWorkspaceDoc(DOC)).toBe(true);
    expect(isWorkspaceDoc(null)).toBe(false);
    expect(isWorkspaceDoc({ ...DOC, version: 2 })).toBe(false);
    expect(isWorkspaceDoc({ ...DOC, pages: [] })).toBe(false);
    expect(isWorkspaceDoc({ ...DOC, active: 2 })).toBe(false);
    expect(isWorkspaceDoc({ ...DOC, active: -1 })).toBe(false);
    expect(isWorkspaceDoc({ ...DOC, pages: [{ name: 1, dock: null }] })).toBe(false);
  });

  it("accepts free pages with their windows, and old pages without a mode (#410)", () => {
    const window = { id: "w1", kind: "watch", params: { link: "pink" }, x: 8, y: 2000, w: 300, h: 500, z: 1 };
    const free = (page: object) => ({ ...DOC, pages: [{ name: "Free", mode: "free", dock: null, ...page }], active: 0 });
    expect(isWorkspaceDoc(free({ canvas: { windows: [window] } }))).toBe(true);
    expect(isWorkspaceDoc(free({ canvas: null }))).toBe(true); // fresh: gets its starter windows
    expect(isWorkspaceDoc(free({}))).toBe(true);
    expect(isWorkspaceDoc(free({ canvas: { windows: [{ ...window, w: "wide" }] } }))).toBe(false);
    expect(isWorkspaceDoc(free({ canvas: { windows: "w1" } }))).toBe(false);
    expect(isWorkspaceDoc(free({ mode: "floating" }))).toBe(false);
    expect(modeOf({ name: "Old", dock: null })).toBe("docked");
    expect(modeOf({ name: "New", mode: "free", dock: null })).toBe("free");
  });
});
