import subprocess, pathlib
S="src/mocktrade/algos/slicer.py"
M=[
 (S,"MODIFY_BUDGET = 24","MODIFY_BUDGET = 25","24 modifications, not 25"),
 (S,"qty = min(planned * lot, ctx.remaining())","qty = ctx.remaining()","never one big order"),
 (S,"if self.limit is not None and (price > self.limit if buying else price < self.limit):","if False:","limit price"),
 (S,"        if self.next_at is None or now < self.next_at:\n            return","        if self.next_at is None:\n            return","slice clock"),
 (S,"            if done >= MODIFY_BUDGET:","            if False:","replace at the budget"),
 (S,"(tick.best_ask if self.cross else tick.best_bid)","(tick.best_bid if self.cross else tick.best_ask)","join vs cross"),
 (S,"    base, extra = divmod(total_lots, slices)\n    return [base + (1 if i < extra else 0) for i in range(slices)]","    base, extra = divmod(total_lots, slices)\n    return [base] * slices","whole lots add up"),
 (S,"        self.next_at = ctx.now()  # paused slices were pulled: carry on at once","        pass","resume carries on"),
]
caught=0
for path,old,new,name in M:
    p=pathlib.Path(path); src=p.read_text()
    if old not in src: print("NOT FOUND",name); continue
    try:
        p.write_text(src.replace(old,new,1))
        ok=subprocess.run(["python3","-m","pytest","-q","-x","tests/test_slicer.py"],capture_output=True,text=True,timeout=300).returncode!=0
    finally:
        p.write_text(src)
    caught+=ok; print(("CAUGHT  " if ok else "MISSED  ")+name, flush=True)
print(f"{caught}/{len(M)} caught")
