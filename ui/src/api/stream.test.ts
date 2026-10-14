import { CLOSE_NOT_LOGGED_IN } from "./stream";
import { fakeStream, tick } from "../test/fakes";

describe("StreamClient subscriptions", () => {
  it("shares one server subscription between widgets on the same instrument", () => {
    const { stream, socket } = fakeStream();
    stream.start();
    socket().open();
    const a = vi.fn();
    const b = vi.fn();
    const stopA = stream.subscribeTicks("NFO:X", a);
    const stopB = stream.subscribeTicks("NFO:X", b);
    expect(socket().sent).toEqual([{ op: "subscribe", instruments: ["NFO:X"] }]);

    socket().receive({ type: "ticks", ticks: [tick("NFO:X", 100)] });
    expect(a).toHaveBeenCalledTimes(1);
    expect(b).toHaveBeenCalledTimes(1);

    stopA();
    expect(socket().sent).toHaveLength(1); // b still watches
    stopA(); // calling twice changes nothing
    expect(stream.watched()).toEqual(["NFO:X"]);
    stopB();
    expect(socket().sent).toEqual([
      { op: "subscribe", instruments: ["NFO:X"] },
      { op: "unsubscribe", instruments: ["NFO:X"] },
    ]);
    expect(stream.watched()).toEqual([]);
  });

  it("gives a second widget the last price at once", () => {
    const { stream, socket } = fakeStream();
    stream.start();
    socket().open();
    stream.subscribeTicks("NFO:X", () => {});
    socket().receive({ type: "ticks", ticks: [tick("NFO:X", 250)] });
    const late = vi.fn();
    stream.subscribeTicks("NFO:X", late);
    expect(late).toHaveBeenCalledWith(expect.objectContaining({ ltp_paise: 250 }));
  });

  it("forgets the cached price when nobody watches, and ignores late ticks", () => {
    const { stream, socket } = fakeStream();
    stream.start();
    socket().open();
    const stop = stream.subscribeTicks("NFO:X", () => {});
    socket().receive({ type: "ticks", ticks: [tick("NFO:X", 250)] });
    stop();
    socket().receive({ type: "ticks", ticks: [tick("NFO:X", 260)] }); // in flight
    const fresh = vi.fn();
    stream.subscribeTicks("NFO:X", fresh);
    expect(fresh).not.toHaveBeenCalled();
  });

  it("subscribes on open for widgets that started before the connection", () => {
    const { stream, socket } = fakeStream();
    stream.subscribeTicks("NFO:A", () => {});
    stream.subscribeTicks("NFO:B", () => {});
    stream.start();
    expect(socket().sent).toEqual([]); // not open yet: nothing sent blind
    socket().open();
    expect(socket().sent).toEqual([{ op: "subscribe", instruments: ["NFO:A", "NFO:B"] }]);
  });
});

describe("StreamClient connection", () => {
  it("reconnects with growing waits and re-subscribes everything", () => {
    const { stream, socket, timers } = fakeStream();
    const statuses: string[] = [];
    stream.onStatus((s) => statuses.push(s));
    stream.start();
    socket().open();
    stream.subscribeTicks("NFO:X", () => {});
    const first = socket();

    first.drop();
    expect(statuses).toEqual(["connecting", "open", "reconnecting"]);
    expect(timers.map((t) => t.ms)).toEqual([500]);
    timers[0]!.fn();
    socket().drop(); // the retry fails too
    expect(timers.map((t) => t.ms)).toEqual([500, 1000]);
    timers[1]!.fn();
    const third = socket();
    expect(third).not.toBe(first);
    third.open();
    expect(third.sent).toEqual([{ op: "subscribe", instruments: ["NFO:X"] }]);
    expect(stream.status).toBe("open");

    third.drop();
    expect(timers[2]!.ms).toBe(500); // a good connection resets the wait
  });

  it("caps the wait at 8 seconds", () => {
    const { stream, socket, timers } = fakeStream();
    stream.start();
    for (let i = 0; i < 8; i += 1) {
      socket().drop();
      timers[timers.length - 1]!.fn();
    }
    expect(timers.map((t) => t.ms)).toEqual([500, 1000, 2000, 4000, 8000, 8000, 8000, 8000]);
  });

  it("stops for good when the server says we are logged out", () => {
    const { stream, socket, timers } = fakeStream();
    stream.start();
    socket().open();
    socket().drop(CLOSE_NOT_LOGGED_IN);
    expect(stream.status).toBe("logged-out");
    expect(timers).toEqual([]);
  });

  it("does not reconnect after stop()", () => {
    const { stream, socket, timers } = fakeStream();
    stream.start();
    socket().open();
    const s = socket();
    stream.stop();
    expect(s.closedWith).toBe(1000);
    s.drop(1000); // the close event arrives later
    expect(timers).toEqual([]);
    expect(stream.status).toBe("stopped");
  });

  it("delivers messages by type and survives a failing listener and bad JSON", () => {
    const { stream, socket } = fakeStream();
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    stream.start();
    socket().open();
    const kill = vi.fn();
    stream.on("kill", () => {
      throw new Error("boom");
    });
    stream.on("kill", kill);
    socket().onmessage?.({ data: "not json" } as MessageEvent);
    socket().receive({ type: "kill", scope: "firm", engaged: true, reason: "r", by: "x", at: null });
    expect(kill).toHaveBeenCalledTimes(1);
    expect(errors).toHaveBeenCalled();
    errors.mockRestore();
  });

  it("records when the last tick arrived", () => {
    const { stream, socket } = fakeStream();
    stream.start();
    socket().open();
    expect(stream.lastTickAt).toBeNull();
    socket().receive({ type: "ticks", ticks: [] });
    expect(stream.lastTickAt).not.toBeNull();
  });
});
