# -*- coding: utf-8 -*-
import sys, io, json, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
os.chdir(os.path.dirname(os.path.abspath(__file__)))

st = json.load(open('data/st_signals.json'))
sigs = st.get('signals', [])
print(f"Total signals: {len(sigs)}")
print()
for s in sigs:
    sym = s['symbol']
    bars = s.get('bars_held', '?')
    price = s.get('current_price', '?')
    signal_at = s.get('signal_at', '?')
    print(f"  {sym}: bars={bars} price={price} signal_at={signal_at}")
