import subprocess
F = {
 "src/mocktrade/options/pricing.py": [
  ("d1 = (math.log(F / K) + 0.5 * v * v) / v", "d1 = (math.log(F / K) - 0.5 * v * v) / v"),
  ("return d1, d1 - v", "return d1, d1 + v"),
  ("df = math.exp(-r * T)\n    if call:", "df = math.exp(r * T)\n    if call:"),
  ("return df * (K * norm_cdf(-d2) - F * norm_cdf(-d1))", "return df * (K * norm_cdf(d2) - F * norm_cdf(d1))"),
  ("delta = df * norm_cdf(d1) if call else -df * norm_cdf(-d1)", "delta = norm_cdf(d1) if call else -norm_cdf(-d1)"),
  ("gamma = df * pdf / (F * sigma * sqrt_t)", "gamma = df * pdf / (F * sigma)"),
  ("theta_per_year = -df * F * pdf * sigma / (2 * sqrt_t) + r * price", "theta_per_year = -df * F * pdf * sigma / (2 * sqrt_t)"),
  ("return Greeks(delta, gamma, theta_per_year / 365.0, vega_per_unit / 100.0)", "return Greeks(delta, gamma, theta_per_year / 252.0, vega_per_unit / 100.0)"),
  ("if price <= intrinsic + 1e-12 or price >= ceiling:", "if price < intrinsic - 1 or price > ceiling * 2:"),
  ("        if diff > 0:\n            hi = sigma", "        if diff < 0:\n            hi = sigma"),
  ("return 0.5 * math.erfc(-x / math.sqrt(2))", "return 0.5 * math.erfc(-x / 2)"),
 ],
 "src/mocktrade/options/model.py": [
  ("CLOSE = time(15, 30)", "CLOSE = time(15, 0)"),
  ("YEAR_SECONDS = 365.0 * 24 * 3600", "YEAR_SECONDS = 252.0 * 24 * 3600"),
  ("return price * math.exp(rate * gap)", "return price"),
  ("return (bid.price_paise + ask.price_paise) / 2", "return float(tick.ltp_paise)"),
  ("abs((f.expiry - option.expiry).days)", "(f.expiry - option.expiry).days"),
 ],
}
caught = total = 0
for f, ms in F.items():
    src = open(f).read()
    for a, b in ms:
        assert a in src, a
        open(f, "w").write(src.replace(a, b, 1)); total += 1
        r = subprocess.run(["python3", "-m", "pytest", "-q", "-x", "tests/test_option_pricing.py"], capture_output=True)
        if r.returncode: caught += 1
        else: print("SURVIVED:", a[:70])
        open(f, "w").write(src)
print(f"{caught}/{total} caught")
