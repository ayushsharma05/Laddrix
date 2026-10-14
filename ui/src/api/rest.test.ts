import { ApiError } from "./rest";
import { fakeRest } from "../test/fakes";

describe("Rest", () => {
  it("sends JSON and returns the parsed answer", async () => {
    const { rest, calls } = fakeRest({ "POST /api/x": { ok: 1 } });
    await expect(rest.post("/api/x", { a: 1 })).resolves.toEqual({ ok: 1 });
    expect(calls).toEqual([{ method: "POST", path: "/api/x", body: { a: 1 } }]);
  });

  it("turns the server's reason into the error message", async () => {
    const { rest } = fakeRest({ "GET /api/x": () => ({ status: 409, body: { detail: "already filled" } }) });
    const err = await rest.get("/api/x").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(409);
    expect((err as ApiError).message).toBe("already filled");
    expect((err as ApiError).loggedOut).toBe(false);
  });

  it("explains validation errors by field", async () => {
    const { rest } = fakeRest({
      "POST /api/x": () => ({ status: 422, body: { detail: [{ loc: ["body", "qty"], msg: "must be > 0" }] } }),
    });
    await expect(rest.post("/api/x", {})).rejects.toThrow("qty: must be > 0");
  });

  it("says when we are logged out or the server is unreachable", async () => {
    const { rest } = fakeRest({
      "GET /api/me": () => ({ status: 401, body: { detail: "please log in" } }),
      "GET /api/down": () => ({ status: 0 }),
    });
    const out = (await rest.get("/api/me").catch((e: unknown) => e)) as ApiError;
    expect(out.loggedOut).toBe(true);
    const down = (await rest.get("/api/down").catch((e: unknown) => e)) as ApiError;
    expect(down.status).toBe(0);
    expect(down.message).toMatch(/can't reach/);
  });

  it("returns nothing for 204", async () => {
    const { rest } = fakeRest({ "DELETE /api/session": () => ({ status: 204 }) });
    await expect(rest.delete("/api/session")).resolves.toBeUndefined();
  });
});
