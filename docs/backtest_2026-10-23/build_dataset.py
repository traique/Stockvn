import pickle, sys, time, logging
sys.path.insert(0, ".")
logging.basicConfig(level=logging.WARNING)
from tradingagents.dataflows import money_flows as mf
from tradingagents.trading import simulator as sim
OUT = "./docs/backtest_2026-10-23/out"
AS_OF = "2025-10-24"
t = time.time()
vn30 = mf.index_members("VN30")
ds = sim.build_dataset(vn30, 900, AS_OF, flows=True, inputs=True)
print("engine dataset", len(ds.symbols), "symbols", len(ds.dates), "sessions", len(ds.inputs), "inputs", ds.skipped, round(time.time()-t), "s", flush=True)
with open(f"{OUT}/ds_engine.pkl", "wb") as f:
    pickle.dump(ds, f)
t = time.time()
vn100 = sorted(set(mf.index_members("VN100")) | set(vn30))
ds2 = sim.build_dataset(vn100, 900, AS_OF, flows=False, inputs=False)
print("factor dataset", len(ds2.symbols), "symbols", len(ds2.dates), ds2.skipped, round(time.time()-t), "s", flush=True)
with open(f"{OUT}/ds_factor.pkl", "wb") as f:
    pickle.dump(ds2, f)
print("DONE")
