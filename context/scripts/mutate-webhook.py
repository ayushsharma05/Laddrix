"""#271 mutation check: break one rule of "a user's webhook reaches only the address that was
checked" (connect to the checked address, TLS against the host name, a fresh check on every try,
IPv4-in-IPv6 judged as IPv4, multicast refused, nonsense refused, no redirects, no pooling, no
proxy, the in-flight and pending bounds, the operator's bound); the tests must notice. Runs the
tests unmutated first. Restores each file. Run alone."""
import subprocess

W = "src/laddrix/alerts/webhook.py"
N = "src/laddrix/ops/notify.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_webhook_pinning.py",
         "tests/test_market_alerts.py"]
MUTANTS = [
 (W, "                r = await self._client.post(target, content=body", "                r = await self._client.post(url, content=body"),
 (W, "    return target, {\"host\": host + port}, {\"sni_hostname\": host}", "    return target, {\"host\": host + port}, {}"),
 (W, "    return target, {\"host\": host + port}, {\"sni_hostname\": host}", "    return target, {}, {\"sni_hostname\": host}"),
 (W, "    where = f\"[{ip}]\" if ip.version == 6 else str(ip)", "    where = str(ip)"),
 (W, "    port = f\":{parsed.port}\" if parsed.port else \"\"", "    port = \"\""),
 (W, "            for attempt in range(len(BACKOFF_S) + 1):\n                address = await self._checked_address(urlparse(url).hostname or \"\")\n", "            address = await self._checked_address(urlparse(url).hostname or \"\")\n            for attempt in range(len(BACKOFF_S) + 1):\n"),
# Not listed: dropping the IPv4-in-IPv6 unwrapping in public(). Python 3.13 already judges
# ::ffff:10.0.0.1 as private, so on 3.13 that mutant is equivalent; the unwrapping stays for the
# Python 3.12 that CI also runs (whose ipaddress may judge it as IPv6).
 (W, "    return ip.is_global and not ip.is_multicast", "    return ip.is_global"),
 (W, "            if not all(public(a) for a in addresses):", "            if not public(addresses[0]):"),
 (W, "        except ValueError:\n            raise WebhookError(\"the webhook host gave", "        except KeyError:\n            raise WebhookError(\"the webhook host gave"),
 (W, "    if ip is not None and not public(str(ip)):", "    if ip is not None and not ip.is_global:"),
 (W, "            timeout=5, follow_redirects=False, trust_env=False,", "            timeout=5, follow_redirects=True, trust_env=False,"),
 (W, "            timeout=5, follow_redirects=False, trust_env=False,", "            timeout=5, follow_redirects=False, trust_env=True,"),
 (W, "            limits=httpx.Limits(max_keepalive_connections=0),", "            limits=httpx.Limits(),"),
 (W, "            async with self._open:\n", "            async with asyncio.Semaphore(1000):\n"),
 (W, "        if self.pending >= MAX_PENDING:\n            return \"too many", "        if False:\n            return \"too many"),
 (W, "        return len(self._tasks) + self._direct", "        return len(self._tasks)"),
 (N, "        if len(self._tasks) >= MAX_PENDING:", "        if False:"),
]
if subprocess.run(TESTS, capture_output=True, timeout=300).returncode != 0:
    raise SystemExit("the tests fail (or a test file is missing) before any mutation: fix that first")
caught = 0
for f, a, b in MUTANTS:
    src = open(f).read()
    if src.count(a) != 1:
        print("NOT UNIQUE OR MISSING:", f, a[:70]); continue
    open(f, "w").write(src.replace(a, b, 1))
    try:
        r = subprocess.run(TESTS, capture_output=True, timeout=300)
        survived = r.returncode == 0
    except subprocess.TimeoutExpired:
        survived = False
    finally:
        open(f, "w").write(src)
    if survived: print("SURVIVED:", f, a[:90].replace(chr(10), " "), "->", b[:40])
    else: caught += 1
print(f"{caught}/{len(MUTANTS)} caught")
