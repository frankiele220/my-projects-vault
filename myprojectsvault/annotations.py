"""Pictures with annotations drawn on them (red boxes, arrows, circles, marker strokes): mostly bug-report screenshots, so the
folder view hides them by default. Found on this Mac with colour and shape only, no model:

1. read the picture about 1000 px wide;
2. keep the strongly coloured pixels in the colours people annotate with (red, orange, pink, magenta, yellow, green), not
   blue, which is the usual colour of links and buttons;
3. remove solid shapes (buttons, banners, badges) by eroding 3 px and growing back: what is left are thin strokes;
4. annotated when the thin strokes make long straight lines (box edges, underlines) or cover a fair part of the picture
   (hand-drawn circles and arrows). UI text in red is thin too, but short and scattered, so it stays under the bar.
"""
from __future__ import annotations

import os

import numpy as np

SIZE = 1000
ERODE = 3            # strokes up to about 6 px thick at 1000 px count as thin
RUN = 40             # a straight thin line at least this long (px at 1000 px) counts as a box edge or underline
MIN_LINE_PX = 160    # total length of such lines that makes a picture annotated
MIN_THIN = 0.002     # or this share of the picture in thin red strokes (hand-drawn circles, arrows)


def _pixels(path: str):
    """RGB array of the picture at most SIZE px on its longer side (macOS ImageIO)."""
    import Quartz
    from Foundation import NSURL
    src = Quartz.CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(path), None)
    if not src:
        return None
    img = Quartz.CGImageSourceCreateThumbnailAtIndex(src, 0, {Quartz.kCGImageSourceCreateThumbnailFromImageAlways: True,
                                                             Quartz.kCGImageSourceThumbnailMaxPixelSize: SIZE,
                                                             Quartz.kCGImageSourceCreateThumbnailWithTransform: True})
    if img is None:
        return None
    w, h = Quartz.CGImageGetWidth(img), Quartz.CGImageGetHeight(img)
    ctx = Quartz.CGBitmapContextCreate(None, w, h, 8, w * 4, Quartz.CGColorSpaceCreateDeviceRGB(),
                                       Quartz.kCGImageAlphaPremultipliedLast)
    Quartz.CGContextSetRGBFillColor(ctx, 1, 1, 1, 1)
    Quartz.CGContextFillRect(ctx, Quartz.CGRectMake(0, 0, w, h))
    Quartz.CGContextDrawImage(ctx, Quartz.CGRectMake(0, 0, w, h), img)
    buf = Quartz.CGBitmapContextGetData(ctx)
    a = np.frombuffer(buf.as_buffer(w * h * 4), dtype=np.uint8).reshape(h, w, 4)
    return a[:, :, :3].astype(np.float32) / 255.0


def _shift_all(m: np.ndarray, r: int, fn) -> np.ndarray:
    """Erosion (fn=np.logical_and) or dilation (fn=np.logical_or) with a (2r+1) square, by shifting."""
    out = m.copy()
    h, w = m.shape
    p = np.pad(m, r, constant_values=False)
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            out = fn(out, p[r + dy:r + dy + h, r + dx:r + dx + w])
    return out


def _runs(m: np.ndarray, min_len: int) -> int:
    """Pixels lying in straight horizontal or vertical runs of at least min_len."""
    total = 0
    for a in (m, m.T):
        d = np.diff(np.pad(a.astype(np.int8), ((0, 0), (1, 1))), axis=1)
        starts, ends = np.where(d == 1), np.where(d == -1)   # row-major order, so the n-th start pairs with the n-th end
        lens = ends[1] - starts[1]
        total += int(lens[lens >= min_len].sum())
    return total


def score(path: str, pure_red: bool = True) -> dict | None:
    """pure_red: only the near-pure red that markup tools (Skitch, Markup, Snagit) draw with by default; brand colours and
    charts are rarely that red. Off: every strong non-blue colour (catches more, but also design lines)."""
    rgb = _pixels(path)
    if rgb is None:
        return None
    mx, mn = rgb.max(axis=2), rgb.min(axis=2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    # hue in degrees
    d = np.maximum(mx - mn, 1e-6)
    hue = np.where(mx == r, (60 * ((g - b) / d)) % 360, np.where(mx == g, 60 * ((b - r) / d) + 120, 60 * ((r - g) / d) + 240))
    strong = (sat > 0.6) & (mx > 0.55)
    if pure_red:
        marker = (r > 0.85) & (g < 0.3) & (b < 0.3) & ((hue >= 345) | (hue <= 12))
    else:
        marker = strong & ~((hue > 185) & (hue < 265))      # not blue
    solid = _shift_all(_shift_all(marker, ERODE, np.logical_and), ERODE, np.logical_or)
    thin = marker & ~solid
    lines = _runs(thin, RUN)
    share = float(thin.mean())
    return {"lines": lines, "thin": round(share, 5), "annotated": bool(lines >= MIN_LINE_PX or share >= MIN_THIN)}


def backfill(db, redo: bool = False, log=print) -> dict:
    """vault annotations: score every kept picture (or only the ones not scored yet)."""
    where = "" if redo else " AND annotated IS NULL"
    rows = db.q(f"SELECT id, file FROM pictures WHERE status IN ('kept','review','approved') AND file!=''{where}")
    n = found = 0
    for i, r in enumerate(rows, 1):
        if not os.path.isfile(r["file"]):
            continue
        try:
            s = score(r["file"])
        except Exception as e:
            log(f"{r['id']}: {e}")
            continue
        if s is None:
            continue
        db.x("UPDATE pictures SET annotated=?, annotated_score=?, annotated_by='rule' WHERE id=? AND COALESCE(annotated_by,'')!='owner'",
             (int(s["annotated"]), f"{s['lines']}/{s['thin']}", r["id"]))
        n += 1
        found += s["annotated"]
        if i % 500 == 0:
            log(f"{i}/{len(rows)}")
    return {"scored": n, "annotated": found}
