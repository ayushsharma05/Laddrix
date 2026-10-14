import { change, changePct, clock, price, qty, rupees, shortName, shortVolume } from "./format";

describe("price", () => {
  it("groups the Indian way and always shows paise", () => {
    expect(price(2501010)).toBe("25,010.10");
    expect(price(562725000)).toBe("56,27,250.00");
    expect(price(5)).toBe("0.05");
    expect(price(0)).toBe("0.00");
  });
  it("uses the real minus sign", () => {
    expect(price(-500)).toBe("−5.00");
  });
});

describe("rupees", () => {
  it("adds the rupee sign, and + only when asked", () => {
    expect(rupees(562725000)).toBe("₹56,27,250.00");
    expect(rupees(150000, { sign: true })).toBe("+₹1,500.00");
    expect(rupees(0, { sign: true })).toBe("₹0.00");
    expect(rupees(-2000000)).toBe("−₹20,000.00");
  });
});

describe("qty", () => {
  it("groups whole numbers", () => {
    expect(qty(75)).toBe("75");
    expect(qty(150000)).toBe("1,50,000");
    expect(qty(-75)).toBe("−75");
  });
});

describe("change and changePct", () => {
  it("are always signed against the previous close", () => {
    expect(change(2501010, 2491480)).toBe("+95.30");
    expect(change(2490000, 2500000)).toBe("−100.00");
    expect(change(100, 100)).toBe("0.00");
    expect(changePct(2501010, 2491480)).toBe("+0.38%");
    expect(changePct(2490000, 2500000)).toBe("−0.40%");
  });
  it("are empty without a usable close", () => {
    expect(change(100, null)).toBe("");
    expect(change(100, undefined)).toBe("");
    expect(changePct(100, 0)).toBe("");
  });
  it("show no direction when the percent rounds to zero", () => {
    expect(changePct(1000001, 1000000)).toBe("0.00%");
  });
});

describe("shortVolume", () => {
  it("uses K, L and Cr", () => {
    expect(shortVolume(950)).toBe("950");
    expect(shortVolume(12500)).toBe("12.5K");
    expect(shortVolume(125000)).toBe("1.25L");
    expect(shortVolume(23000000)).toBe("2.3Cr");
    expect(shortVolume(100000)).toBe("1L");
  });
});

describe("shortName", () => {
  it("drops the exchange", () => {
    expect(shortName("NFO:NIFTY26OCTFUT")).toBe("NIFTY26OCTFUT");
    expect(shortName("SBIN")).toBe("SBIN");
  });
});

describe("clock", () => {
  it("shows India time, with or without milliseconds", () => {
    expect(clock("2026-10-14T04:40:05.123+00:00")).toBe("10:10:05.123");
    expect(clock("2026-10-14T04:40:05.123+00:00", false)).toBe("10:10:05");
    expect(clock("2026-10-14T18:45:00Z", false)).toBe("00:15:00");
  });
  it("never crashes on bad input", () => {
    expect(clock("not a time")).toBe("–");
  });
});
