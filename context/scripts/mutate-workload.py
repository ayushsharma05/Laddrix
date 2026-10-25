"""#274 mutation check: break one rule of "research and report work never holds up trading"
(a backtest's order rate in replayed time, the worker process, its memory cap, time limit and
recovery, the waiting limits, cancelling only the run asked for, reports in a thread, jobs in
tasks of their own); the tests must notice. Runs the tests unmutated first. Restores each file.
Run alone."""
import subprocess

B = "src/mocktrade/research/backtest.py"
R = "src/mocktrade/reports/service.py"
ROUTES_PORTFOLIO = "src/mocktrade/server/routes/portfolio.py"
ROUTES_RESEARCH = "src/mocktrade/server/routes/research.py"
S = "src/mocktrade/ops/scheduler.py"
TESTS = ["python3", "-m", "pytest", "-q", "-x", "tests/test_workload.py"]
MUTANTS = [
 (B, "        algo_types=BUILTIN, clock=clock, throttle=throttle,", "        algo_types=BUILTIN, clock=clock,"),
 (B, "            if broker.idle and (not rt.algos.busy or asleep[\"n\"]):  # a wait needs the clock", "            if broker.idle and not rt.algos.busy:"),
 (B, "        if not isinstance(source, RecordedDay | CandleDay):  # an iterator can't be handed over", "        if True:"),
 (B, "        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))", "        pass"),
 (B, "            return await asyncio.wait_for(job, self._timeout_s)", "            return await job"),
 (B, "        except TimeoutError:\n            self.stop()\n", "        except TimeoutError:\n"),
 (B, "        except concurrent.futures.process.BrokenProcessPool:\n            self.stop()\n", "        except concurrent.futures.process.BrokenProcessPool:\n"),
 (B, "        if len(waiting) >= MAX_WAITING:", "        if False:"),
 (B, "        if sum(r.owner == owner for r in waiting) >= MAX_PER_USER:", "        if False:"),
 (B, "            if run.state == \"running\":  # only its own day: a queued one stops nobody's", "            if True:"),
 (B, "            run.state, run.error = \"cancelled\", \"cancelled\"", "            run.state, run.error = \"failed\", \"cancelled\""),
 (B, "        task.cancel()\n        await asyncio.wait({task}, timeout=5)", "        task.cancel()"),
 (B, "        for process in list((pool._processes or {}).values()):\n            process.kill()", "        for process in []:\n            process.kill()"),
 (ROUTES_RESEARCH, "        except BacktestBusy as exc:  # a bounded queue (#274): later, not never\n            raise HTTPException(429", "        except BacktestBusy as exc:  # a bounded queue (#274): later, not never\n            raise HTTPException(422"),
 (ROUTES_RESEARCH, "        if not await backtest_runs().cancel(run_id):", "        if not await backtest_runs().cancel(run_id) and False:"),
 (ROUTES_PORTFOLIO, "            data = await asyncio.to_thread(day_reports.day, account_id, when)", "            data = day_reports.day(account_id, when)"),
 (R, "                report = await asyncio.to_thread(self._build, account_id, day, prices=True,\n                                                 final=True)  # fmt: skip", "                report = self._build(account_id, day, prices=True, final=True)"),
 (S, "        if busy is not None and not busy.done():", "        if False:"),
 (S, "        if started:\n            await asyncio.wait(started, timeout=WAIT_S)", "        for t in started:\n            await t"),
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
