"""vault uncover: put the original back for pictures that were saved with covered areas (the owner picks what to cover later).
Each picture is read again from its own document on the archive drive; nothing leaves this Mac."""
from __future__ import annotations

import os
import zipfile

from . import config, vision
from .db import DB, now


def original(source: str, member: str):
    """The picture as it is in its document (CGImage), or None."""
    if not member:
        with open(source, "rb") as f:
            return vision.from_bytes(f.read())
    if member.startswith("page "):
        n = int(member.split()[1])
        for k, img in vision.pdf_pages(source):
            if k == n:
                return img
        return None
    with zipfile.ZipFile(source) as z:
        return vision.from_bytes(z.read(member))


def run(db: DB, log=print) -> dict:
    rows = db.q("SELECT id, sha, source, member, file FROM pictures WHERE status IN ('kept','review','approved') "
                "AND file!='' AND redactions>0")
    done = missing = 0
    thumbs = os.path.join(config.PICTURES, "_thumbs")
    for i, r in enumerate(rows, 1):
        try:
            img = original(r["source"], r["member"]) if os.path.exists(r["source"]) else None
        except Exception:
            img = None
        if img is None:
            missing += 1
            continue
        vision.save(img, r["file"])
        try:
            os.remove(os.path.join(thumbs, r["sha"] + ".jpg"))
        except OSError:
            pass
        db.x("UPDATE pictures SET redactions=0, updated_at=? WHERE id=?", (now(), r["id"]))
        done += 1
        if i % 200 == 0:
            log(f"{i}/{len(rows)}")
    return {"uncovered": done, "not_found": missing, "total": len(rows)}
