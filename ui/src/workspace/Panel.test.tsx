import { act, render, screen } from "@testing-library/react";
import type { IDockviewPanelHeaderProps } from "dockview-react";
import { ControllerContext, WidgetTab } from "./Panel";
import type { WorkspaceController } from "./controller";
import type { BaseParams } from "./registry";
import "../widgets/ladder/Ladder";

/** Just enough of dockview's panel api for a tab. */
function fakeApi(title: string) {
  const titleListeners = new Set<(e: { title: string }) => void>();
  const api = {
    title,
    component: "ladder",
    group: { api: { location: { type: "grid" } } },
    onDidLocationChange: () => ({ dispose() {} }),
    onDidGroupChange: () => ({ dispose() {} }),
    onDidTitleChange: (fn: (e: { title: string }) => void) => {
      titleListeners.add(fn);
      return { dispose: () => titleListeners.delete(fn) };
    },
    setTitle(next: string) {
      api.title = next;
      for (const fn of titleListeners) fn({ title: next });
    },
    isMaximized: () => false,
    close() {},
  };
  return api;
}

describe("a widget's tab", () => {
  it("follows the panel's title: a linked widget that switches instrument renames its tab", () => {
    const api = fakeApi("NIFTY26NOVFUT");
    const props = { api, params: { link: "pink" } } as unknown as IDockviewPanelHeaderProps<BaseParams>;
    const view = render(
      <ControllerContext.Provider value={{ changed() {} } as unknown as WorkspaceController}>
        <WidgetTab {...props} />
      </ControllerContext.Provider>,
    );
    expect(screen.getByText("NIFTY26NOVFUT")).toBeTruthy();
    act(() => api.setTitle("NIFTY26OCTFUT"));
    expect(screen.getByText("NIFTY26OCTFUT")).toBeTruthy();
    view.unmount();
  });
});
