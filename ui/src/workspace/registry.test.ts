import "../widgets";
import { widgetsFor } from "./registry";

describe("Widgets ▾ by role", () => {
  const kinds = (roles: string[]) => widgetsFor(roles).map((w) => w.kind);
  it("offers the Admin widget only to admin and risk users", () => {
    expect(kinds(["trader"])).not.toContain("admin");
    expect(kinds([])).not.toContain("admin");
    expect(kinds(["admin"])).toContain("admin");
    expect(kinds(["risk"])).toContain("admin");
    expect(kinds(["trader"])).toContain("ticket"); // widgets without a role list are for everyone
    expect(kinds(["admin", "trader"]).length).toBe(kinds(["trader"]).length + 1);
  });
});
