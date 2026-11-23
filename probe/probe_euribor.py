import json, sys
sys.path.insert(0, "scripts")
import build_markets as bm
try:
    out = bm.euribor_block()
except Exception as e:
    out = {"err": repr(e)}
json.dump(out, open("probe/result.json", "w"), indent=1)
