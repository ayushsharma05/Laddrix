import subprocess, pathlib
S="src/laddrix/algos/spreader.py"; E="src/laddrix/algos/engine.py"
M=[
 (S,"price = (math.floor(raw / tick) if buying else math.ceil(raw / tick)) * tick","price = round(raw / tick) * tick","rounding never worse than the level"),
 (S,"hedge_level = b.best_bid if buying else b.best_ask","hedge_level = b.best_ask if buying else b.best_bid","quote against the hedge side"),
 (S,"price = level.price_paise + (-1 if side is Side.SELL else 1) * self.payup * tick","price = level.price_paise + (-1 if side is Side.SELL else 1) * (self.payup + 1) * tick","pay-up limit"),
 (S,"        return ctx.filled(ctx.instrument_id) >= ctx.qty and self._unhedged(ctx) == 0","        return ctx.filled(ctx.instrument_id) >= ctx.qty","done only when hedged"),
 (S,"        return child.request.instrument_id == self.leg2  # hedges reduce risk: keep them","        return False","stop keeps hedges"),
 (S,"        unhedged = self._unhedged(ctx)\n        if unhedged == 0:\n            return None","        return None\n        unhedged = 0\n        if unhedged == 0:\n            return None","unhedged alert"),
 (S,"        self._hedge(ctx)\n        self._report(ctx)","        self._report(ctx)","hedge on every update"),
 (S,"        need = unhedged - covering","        need = unhedged","no double hedge"),
 (S,"        if b.lot_size != a.lot_size:","        if False:","same lot size"),
 (E,"        if not hedge and (p.state is not AlgoState.WORKING or self.held):","        if p.state is not AlgoState.WORKING or self.held:","hedges allowed while stopping"),
 (E,"            if p.rejects_in_a_row >= MAX_REJECTS_IN_A_ROW and not p.gave_up:","            if p.rejects_in_a_row >= MAX_REJECTS_IN_A_ROW and p.state is AlgoState.WORKING:","give up while stopping"),
 (E,"        while pending := [t for t in self._tasks if not t.done()]:","        while pending := list(self._tasks):","settle"),
]
caught=0
for path,old,new,name in M:
    p=pathlib.Path(path); src=p.read_text()
    if old not in src: print("NOT FOUND",name); continue
    try:
        p.write_text(src.replace(old,new,1))
        try:
            ok=subprocess.run(["python3","-m","pytest","-q","-x","tests/test_spreader.py","tests/test_algo_engine.py"],capture_output=True,text=True,timeout=60).returncode!=0
        except subprocess.TimeoutExpired:
            ok=True  # hung: the tests didn't pass
    finally:
        p.write_text(src)
    caught+=ok; print(("CAUGHT  " if ok else "MISSED  ")+name, flush=True)
print(f"{caught}/{len(M)} caught")
