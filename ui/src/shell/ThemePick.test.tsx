import { fireEvent, render, screen } from "@testing-library/react";
import { AppProvider, createApp } from "../app/context";
import { fakeRest, fakeStream } from "../test/fakes";
import { ME } from "../test/fixtures";
import type { ThemeEnv } from "../theme/theme";
import { ThemePick } from "./ThemePick";

const PATH = "/api/me/settings/theme";

function setup(put: () => { status: number; body?: unknown } = () => ({ status: 200, body: {} })) {
  const server = fakeRest({ [`PUT ${PATH}`]: put });
  const root = document.createElement("html");
  const env: ThemeEnv = { prefersLight: () => false, onSystemChange: () => () => undefined, root: () => root, storage: () => null };
  const app = createApp({ rest: server.rest, stream: fakeStream().stream, themeEnv: env });
  app.store.setMe(ME);
  render(
    <AppProvider app={app}>
      <ThemePick />
    </AppProvider>,
  );
  return { ...server, app, root };
}

describe("Theme picker", () => {
  it("offers every theme; a pick shows at once and is saved", async () => {
    const { calls, root } = setup();
    const pick = screen.getByRole("combobox", { name: "Theme" }) as HTMLSelectElement;
    expect([...pick.options].map((o) => o.value)).toEqual(["system", "dark", "light", "cb"]);
    expect(pick.value).toBe("system");
    fireEvent.change(pick, { target: { value: "cb" } });
    expect(root.dataset.theme).toBe("cb");
    await vi.waitFor(() => expect(calls.filter((c) => c.method === "PUT")).toHaveLength(1));
    expect(pick.value).toBe("cb");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("a refused save says why and shows the old theme; a later save clears the message", async () => {
    let refuse = true;
    const { root } = setup(() => (refuse ? { status: 403, body: { detail: "not allowed" } } : { status: 200, body: {} }));
    const pick = screen.getByRole("combobox", { name: "Theme" }) as HTMLSelectElement;
    fireEvent.change(pick, { target: { value: "light" } });
    await vi.waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/^Theme not saved: /));
    expect(pick.value).toBe("system");
    expect(root.dataset.theme).toBe("dark");
    refuse = false;
    fireEvent.change(pick, { target: { value: "light" } });
    expect(screen.queryByRole("alert")).toBeNull();
    await vi.waitFor(() => expect(pick.value).toBe("light"));
  });
});
