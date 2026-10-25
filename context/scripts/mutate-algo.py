import subprocess, pathlib
E="src/mocktrade/algos/engine.py"
M=[
 (E,'        return max(0, p.qty - self.filled(p.instrument_id, p.side) - working - sent)','        return max(0, p.qty - self.filled(p.instrument_id, p.side) - working)',"in-flight counts against the parent"),
 (E,"if p.state is not AlgoState.STOPPING or p.in_flight:","if p.state is not AlgoState.STOPPING:","stop waits for in-flight children"),
 (E,'            if p.state is not AlgoState.WORKING and not self._keeps(p, order):\n                # sent just before','            if False:\n                # sent just before',"late child pulled"),
 (E,'        if not hedge and (p.state is not AlgoState.WORKING or self.held):','        if not hedge and p.state is not AlgoState.WORKING:',"unknown outcome holds"),
 (E,'            if p.rejects_in_a_row >= MAX_REJECTS_IN_A_ROW and not p.gave_up:','            if False:',"repeated refusals fail"),
 (E,'            log.exception("algo %s failed", p.algo_id)\n            self.stop_parent(p, f"the algo\'s own code failed: {exc!r}", final=AlgoState.FAILED)','            log.exception("algo %s failed", p.algo_id)',"broken strategy fails"),
 (E,'        if event.kind is EventKind.FILL and self._is_done(p):','        if False:',"done when filled"),
 (E,'            if child.status.is_final or child.pending is Pending.CANCEL:\n                continue\n            if not self._keeps','            if True:\n                continue\n            if not self._keeps',"pull children"),
 ("src/mocktrade/platform.py","                engine.stop_all(f\"trading stopped: {state.reason}\")","                pass","kill switch stops algos"),
 ("src/mocktrade/risk/throttle.py","return await self._admit(Kind.NEW, waits=request.from_algo)","return await self._admit(Kind.NEW, waits=request.algo_id is not None)","children wait in the throttle"),
 ("src/mocktrade/algos/spec.py","        problems.append(f\"{spec.name} has no setting called {name!r}\")","        pass","unknown settings refused"),
 (E,"            algo_id=p.exchange_algo_id,","            algo_id=p.algo_id,","no invented exchange id"),
]
caught=0
for path,old,new,name in M:
    p=pathlib.Path(path); src=p.read_text()
    if old not in src: print("NOT FOUND",name); continue
    try:
        p.write_text(src.replace(old,new,1))
        ok=subprocess.run(["python3","-m","pytest","-q","-x","tests/test_algo_engine.py","tests/test_algo_paper.py"],capture_output=True,text=True,timeout=300).returncode!=0
    finally:
        p.write_text(src)
    caught+=ok; print(("CAUGHT  " if ok else "MISSED  ")+name, flush=True)
print(f"{caught}/{len(M)} caught")
