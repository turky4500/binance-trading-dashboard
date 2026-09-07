# -*- coding: utf-8 -*-
import sys, io, json, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Check current signal board
st = json.load(open('data/st_signals.json'))
sigs = st.get('signals', [])
fresh = [s for s in sigs if s.get('bars_held', 99) <= 1]
print(f"Total signals: {len(sigs)}")
print(f"Fresh (bars_held <= 1): {len(fresh)}")
for s in fresh:
    print(f"  {s['symbol']}: bars={s.get('bars_held')} price={s.get('current_price')}")

print()

# Check recent dedup
try:
    recent = json.load(open('data/whatsapp_st_recent.json'))
    print(f"Dedup entries: {len(recent)}")
    for sym, ts in sorted(recent.items(), key=lambda x: x[1], reverse=True)[:10]:
        print(f"  {sym}: {ts}")
except FileNotFoundError:
    print("Dedup file not found locally")
