// Chart test data, shared by the model, drawing and widget tests.

import type { Candle } from "../../api/types";

export function candle(t: string, o: number, h = o, l = o, c = o, v = 10): Candle {
  return { t, o, h, l, c, v, oi: null };
}
