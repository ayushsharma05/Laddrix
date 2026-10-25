import { fireEvent, render, screen } from "@testing-library/react";
import { ContextMenu, type MenuItem } from "./ContextMenu";

function setup(items: MenuItem[]) {
  const onClose = vi.fn();
  render(<ContextMenu x={40} y={50} items={items} onClose={onClose} />);
  return { onClose };
}

describe("the right-click menu", () => {
  it("chooses an item with a click, and closes", () => {
    const save = vi.fn();
    const { onClose } = setup([{ header: "Page: Main" }, { label: "Save layout now", hint: "Ctrl+S", onSelect: save }]);
    expect(screen.getByText("Page: Main")).toBeTruthy();
    fireEvent.click(screen.getByRole("menuitem", { name: /Save layout now/ }));
    expect(save).toHaveBeenCalledOnce();
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("a click outside calls the newest close handler, even one given after it opened", () => {
    const first = vi.fn();
    const second = vi.fn();
    const items = [{ label: "One", onSelect: () => {} }];
    const view = render(<ContextMenu x={40} y={50} items={items} onClose={first} />);
    view.rerender(<ContextMenu x={40} y={50} items={items} onClose={second} />);
    fireEvent.pointerDown(document.body);
    expect(second).toHaveBeenCalledOnce();
    expect(first).not.toHaveBeenCalled();
  });

  it("is drawn where the pointer is", () => {
    setup([{ label: "One", onSelect: () => {} }]);
    const menu = screen.getByRole("menu");
    expect([menu.style.left, menu.style.top]).toEqual(["40px", "50px"]);
  });

  it("does nothing for a disabled item, and marks the chosen option", () => {
    const close = vi.fn();
    const { onClose } = setup([
      { label: "Close", disabled: true, onSelect: close },
      { label: "Pink", checked: true, onSelect: () => {} },
      { label: "Blue", checked: false, onSelect: () => {} },
    ]);
    fireEvent.click(screen.getByRole("menuitem", { name: /Close/ }));
    expect(close).not.toHaveBeenCalled();
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("menuitemcheckbox", { name: /Pink/ }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByRole("menuitemcheckbox", { name: /Blue/ }).getAttribute("aria-checked")).toBe("false");
    expect(screen.getByRole("menuitemcheckbox", { name: /Pink/ }).textContent).toContain("✓");
    expect(screen.getByRole("menuitemcheckbox", { name: /Blue/ }).textContent).not.toContain("✓");
  });

  it("works from the keyboard: arrows, a submenu, back, Enter", () => {
    const pick = vi.fn();
    const { onClose } = setup([
      { label: "Search product…", onSelect: () => {} },
      "separator",
      { label: "Lots", items: [{ label: "1", onSelect: () => {} }, { label: "5", onSelect: pick }] },
    ]);
    const first = screen.getByRole("menuitem", { name: /Search product/ });
    expect(document.activeElement).toBe(first); // the first item has focus
    fireEvent.keyDown(first, { key: "ArrowDown" });
    const lots = screen.getByRole("menuitem", { name: /Lots/ });
    expect(document.activeElement).toBe(lots);
    fireEvent.keyDown(lots, { key: "ArrowRight" });
    expect(lots.getAttribute("aria-expanded")).toBe("true");
    const one = screen.getByRole("menuitem", { name: "1" });
    expect(document.activeElement).toBe(one);
    fireEvent.keyDown(one, { key: "ArrowLeft" });
    expect(document.activeElement).toBe(lots);
    expect(screen.queryByRole("menuitem", { name: "1" })).toBeNull();
    fireEvent.keyDown(lots, { key: "ArrowRight" });
    fireEvent.keyDown(screen.getByRole("menuitem", { name: "1" }), { key: "ArrowDown" });
    fireEvent.click(document.activeElement!);
    expect(pick).toHaveBeenCalledOnce();
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("opens a submenu on hover", () => {
    setup([{ label: "Theme", items: [{ label: "Dark", onSelect: () => {} }] }]);
    fireEvent.pointerEnter(screen.getByRole("menuitem", { name: /Theme/ }));
    expect(screen.getByRole("menuitem", { name: "Dark" })).toBeTruthy();
  });

  it("closes on Esc and on a click outside", () => {
    const { onClose } = setup([{ label: "One", onSelect: () => {} }]);
    fireEvent.keyDown(screen.getByRole("menuitem", { name: /One/ }), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.pointerDown(document.body);
    expect(onClose).toHaveBeenCalledTimes(2);
    fireEvent.pointerDown(screen.getByRole("menuitem", { name: /One/ })); // inside: stays
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
