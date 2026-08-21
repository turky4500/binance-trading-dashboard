#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate PWA icons (192 + 512 PNG) for the dashboard.
Run once: python scripts/gen_icons.py  ->  frontend/icons/icon-192.png / icon-512.png
The PNGs are committed as static assets (this script is only for regeneration)."""
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'frontend', 'icons')


def draw_icon(size):
    img = Image.new('RGBA', (size, size), (11, 14, 17, 255))
    d = ImageDraw.Draw(img)
    # panel
    d.rounded_rectangle([size * 0.06, size * 0.06, size * 0.94, size * 0.94],
                        radius=size * 0.12, fill=(19, 24, 32, 255))
    # candles (green / red / green)
    cw = size * 0.09
    xs = [0.22, 0.42, 0.62]
    tops = [0.52, 0.34, 0.22]
    bots = [0.72, 0.58, 0.40]
    colors = [(22, 199, 132, 255), (234, 57, 67, 255), (22, 199, 132, 255)]
    for x, t, b, c in zip(xs, tops, bots, colors):
        d.rectangle([size * x, size * t, size * x + cw, size * b], fill=c)
        d.rectangle([size * x + cw * 0.4, size * (t - 0.10), size * x + cw * 0.6, size * t], fill=c)  # wick top
        d.rectangle([size * x + cw * 0.4, size * b, size * x + cw * 0.6, size * (b + 0.08)], fill=c)  # wick bottom
    # ascending gold line
    pts = [(size * 0.14, size * 0.66), (size * 0.34, size * 0.50),
           (size * 0.52, size * 0.56), (size * 0.86, size * 0.24)]
    d.line(pts, fill=(240, 185, 11, 255), width=max(3, size // 60))
    r = max(4, size // 24)
    for x, y in pts:
        d.ellipse([x - r, y - r, x + r, y + r], fill=(240, 185, 11, 255))
    return img


def main():
    os.makedirs(OUT, exist_ok=True)
    for s in (512, 192):
        p = os.path.join(OUT, f'icon-{s}.png')
        draw_icon(s).resize((s, s), Image.LANCZOS).save(p)
        print('wrote', p, os.path.getsize(p), 'bytes')


if __name__ == '__main__':
    main()
