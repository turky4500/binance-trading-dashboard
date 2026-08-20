# -*- coding: utf-8 -*-
"""
Local/development web server for the dashboard.

Serves:
  /            -> frontend/index.html
  /css, /js    -> frontend assets
  /data        -> JSON data written by the analyzer (opportunities, meta, ...)

The frontend only ever reads public market-data JSON. No trading anywhere.
"""
import os

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

app = FastAPI(title="Binance Trading Dashboard", docs_url=None, redoc_url=None)

if os.path.isdir(os.path.join(ROOT, "data")):
    app.mount("/data", StaticFiles(directory=os.path.join(ROOT, "data")), name="data")
app.mount("/css", StaticFiles(directory=os.path.join(ROOT, "frontend", "css")), name="css")
app.mount("/js", StaticFiles(directory=os.path.join(ROOT, "frontend", "js")), name="js")


@app.get("/")
async def index():
    return FileResponse(os.path.join(ROOT, "frontend", "index.html"))


@app.get("/api/health")
async def health():
    meta_path = os.path.join(ROOT, "data", "meta.json")
    if not os.path.exists(meta_path):
        return JSONResponse({"ok": False, "detail": "no data yet — run: python -m analyzer.run"})
    import json
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    return JSONResponse({"ok": True, "meta": meta})
