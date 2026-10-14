import subprocess
F = "src/mocktrade/options/strategy.py"
M = [
 ('return 1 if self.side == "BUY" else -1', 'return 1'),
 ('return self.sign * self.lots * self.instrument.lot_size', 'return self.sign * self.lots'),
 ('intrinsic = max(s - k, 0.0) if leg.instrument.kind is CE else max(k - s, 0.0)', 'intrinsic = max(s - k, 0.0)'),
 ('return float(sum(leg.units for leg in legs if leg.instrument.kind is CE))', 'return float(sum(leg.units for leg in legs))'),
 ('"max_profit": "unlimited" if slope > 0 else round(best, 2),', '"max_profit": round(best, 2),'),
 ('"max_loss": "unlimited" if slope < 0 else round(worst, 2),', '"max_loss": round(worst, 2),'),
 ('found.append(round(kinks[-1] - last / slope, 2))', 'pass'),
 ('points = [0.0, *kinks]\n    values', 'points = [*kinks]\n    values'),
 ('premium = -sum(leg.units * leg.price_paise / 100 for leg in legs)', 'premium = sum(leg.units * leg.price_paise / 100 for leg in legs)'),
 ('index = atm_index + recipe.offset * width', 'index = atm_index + recipe.offset'),
 ('"lots": lots * recipe.ratio}', '"lots": lots}'),
 ('horizon = min(leg.instrument.expiry for leg in legs)', 'horizon = max(leg.instrument.expiry for leg in legs)'),
 ('if len(underlyings) != 1:', 'if False:'),
]
src = open(F).read(); caught = 0
for a, b in M:
    assert a in src, a
    open(F, "w").write(src.replace(a, b, 1))
    r = subprocess.run(["python3", "-m", "pytest", "-q", "-x", "tests/test_option_strategy.py"], capture_output=True)
    if r.returncode: caught += 1
    else: print("SURVIVED:", a[:70])
    open(F, "w").write(src)
print(f"{caught}/{len(M)} caught")
