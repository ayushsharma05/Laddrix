import { render, screen } from "@testing-library/react";
import { AppProvider, createApp } from "../app/context";
import { fakeRest, fakeStream } from "../test/fakes";
import { ME } from "../test/fixtures";
import { ServerVersion } from "./ServerVersion";

function setup(health: unknown) {
  const server = fakeRest({ "GET /api/health": health as object });
  const app = createApp({ rest: server.rest, stream: fakeStream().stream });
  app.store.setMe(ME);
  return render(
    <AppProvider app={app}>
      <ServerVersion />
    </AppProvider>,
  );
}

describe("Server version", () => {
  it("shows the version the server reports, with its build on hover", async () => {
    setup({ status: "ok", version: "1.0.0-rc.1", build: "abc1234" });
    const shown = await screen.findByText("v1.0.0-rc.1");
    expect(shown.getAttribute("title")).toBe("Laddrix 1.0.0-rc.1, build abc1234");
  });

  it("shows nothing when the server doesn't say (no guess)", async () => {
    const { container } = setup({ status: "ok" });
    await new Promise((r) => setTimeout(r, 20));
    expect(container.textContent).toBe("");
  });
});
