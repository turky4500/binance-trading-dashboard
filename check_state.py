# -*- coding: utf-8 -*-
import sys, io, json, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Check current signal board
st = json.load(open('data/st_signals.json'))
sigs = st.get('signals', [])
fresh = [s for s in sigs if s.get('bars_held', 99) <= 2]
print(f"Total signals: {len(sigs)}")
print(f"Fresh (bars_held <= 2): {len(fresh)}")
for s in fresh:
    print(f"  {s['symbol']}: bars={s.get('bars_held')} price={s.get('current_price')}")

# Check update log for recent CI runs
print("\n--- Recent CI runs ---")
try:
    log = json.load(open('data/update_log.json'))
    runs = log if isinstance(log, list) else log.get('runs', [])
    for r in runs[-5:]:
        print(f"  {r.get('ts', '?')}: st_new={r.get('st_new_count', '?')} sent={r.get('whatsapp_st_new_count', '?')}")
except Exception as e:
    print(f"  Error: {e}")
