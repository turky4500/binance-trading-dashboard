#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build script.

  1) site/   -> full static site for GitHub Pages: frontend assets + data JSONs
                (the page fetches ./data/*.json — works online, auto-refreshes)
  2) dist/   -> single self-contained index.html with data embedded inline
                (works offline / file:// / sandboxed previews with no network)
"""
import json
import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND = os.path.join(ROOT, 'frontend')
DATA = os.path.join(ROOT, 'data')
SITE = os.path.join(ROOT, 'site')
DIST = os.path.join(ROOT, 'dist')


def read(p):
    with open(p, 'r', encoding='utf-8') as f:
        return f.read()


def load_data():
    def j(name, default=None):
        p = os.path.join(DATA, name)
        if os.path.exists(p):
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
        return default
    return {
        'opportunities': j('opportunities.json', []),
        'meta': j('meta.json'),
        'market': j('market.json'),
        'performance': j('performance.json'),
        'history': j('history.json', []),
    }


def build_site():
    if os.path.exists(SITE):
        shutil.rmtree(SITE)
    shutil.copytree(FRONTEND, SITE)
    if os.path.exists(DATA):
        shutil.copytree(DATA, os.path.join(SITE, 'data'))
    print(f"site/ built ({sum(len(f) for _, _, f in os.walk(SITE) for f in f)} files)")


def build_dist():
    os.makedirs(DIST, exist_ok=True)
    html = read(os.path.join(FRONTEND, 'index.html'))
    css = read(os.path.join(FRONTEND, 'css', 'style.css'))
    js = (
        read(os.path.join(FRONTEND, 'js', 'i18n.js')) + '\n' +
        read(os.path.join(FRONTEND, 'js', 'chart.js')) + '\n' +
        read(os.path.join(FRONTEND, 'js', 'app.js'))
    )
    data = load_data()
    embedded = json.dumps(data, ensure_ascii=False)
    # replace external references with inline content
    html = html.replace('<link rel="stylesheet" href="css/style.css">', f'<style>{css}</style>')
    for src in ('js/i18n.js', 'js/chart.js', 'js/app.js'):
        html = html.replace(f'<script src="{src}"></script>', '')
    html = html.replace('</body>', f'<script>window.__EMBEDDED__ = {embedded};</script>\n<script>{js}</script>\n</body>')
    out = os.path.join(DIST, 'index.html')
    with open(out, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"dist/index.html built ({os.path.getsize(out) // 1024} KB, embedded snapshot)")


if __name__ == '__main__':
    build_site()
    build_dist()
