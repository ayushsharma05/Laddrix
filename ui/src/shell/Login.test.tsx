import { fireEvent, render, screen } from "@testing-library/react";
import { AppProvider, createApp } from "../app/context";
import { fakeRest, fakeStream } from "../test/fakes";
import { ME } from "../test/fixtures";
import { Login } from "./Login";

function setup(answer: () => { status: number; body?: unknown }) {
  const server = fakeRest({ "POST /api/session": answer });
  const app = createApp({ rest: server.rest, stream: fakeStream().stream });
  render(
    <AppProvider app={app}>
      <Login />
    </AppProvider>,
  );
  const fill = (code: string) => {
    fireEvent.change(screen.getByLabelText("Username"), { target: { value: " demo " } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "pw" } });
    fireEvent.change(screen.getByLabelText("Authenticator code"), { target: { value: code } });
  };
  return { ...server, app, fill };
}

describe("Login", () => {
  it("only accepts a 6-digit code", () => {
    const { fill } = setup(() => ({ status: 200, body: ME }));
    fill("12a34");
    expect((screen.getByLabelText("Authenticator code") as HTMLInputElement).value).toBe("1234");
    expect((screen.getByRole("button", { name: "Sign in" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("signs in with the trimmed username", async () => {
    const { fill, calls, app } = setup(() => ({ status: 200, body: ME }));
    fill("123456");
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await vi.waitFor(() => expect(app.store.getState().me?.user.id).toBe("ayush"));
    expect(calls[0]!.body).toEqual({ username: "demo", password: "pw", code: "123456" });
  });

  it("shows the server's refusal and clears the code", async () => {
    const { fill } = setup(() => ({ status: 401, body: { detail: "wrong username, password or code" } }));
    fill("123456");
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect((await screen.findByRole("alert")).textContent).toBe("wrong username, password or code");
    expect((screen.getByLabelText("Authenticator code") as HTMLInputElement).value).toBe("");
  });
});
