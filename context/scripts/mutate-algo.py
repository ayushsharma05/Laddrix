import subprocess, pathlib
E="src/mocktrade/algos/engine.py"
M=[
 (E,"return max(0, self._parent.qty - self.filled(main) - working - sent)","return max(0, self._parent.qty - self.filled(main) - working)","in-flight counts against the parent"),
 (E,"if p.state is not AlgoState.STOPPING or p.in_flight:","if p.state is not AlgoState.STOPPING:","stop waits for in-flight children"),
 (E,"            if p.state is not AlgoState.WORKING:\n                # sent just before","            if False:\n                # sent just before","late child pulled"),
 (E,"if p.state is not AlgoState.WORKING or self.held or qty <= 0:","if p.state is not AlgoState.WORKING or qty <= 0:","unknown outcome holds"),
 (E,"            if p.rejects_in_a_row >= MAX_REJECTS_IN_A_ROW and p.state is AlgoState.WORKING:","            if False:","repeated refusals fail"),
 (E,"            self.stop_parent(p, f\"the algo's own code failed: {exc!r}\", final=AlgoState.FAILED)","            pass","broken strategy fails"),
 (E,"        if event.kind is EventKind.FILL and self._main_filled(p) >= p.qty:","        if False:","done when filled"),
 (E,"            if not child.status.is_final and child.pending is not Pending.CANCEL:","            if False:","pull children"),
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
