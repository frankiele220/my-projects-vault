"""vault dates: each portfolio project's created_at = the earliest date among its pictures' documents and its videos.

Dates are the files' own modified dates on the archive drive (they span 2008 to 2026 and match the dates in folder names).
Videos are not sorted into projects yet, so a video counts for a project only when one of its folders is named after exactly
that project ("160103 - Acme v2"). A project with no pictures or videos takes 1 January of the first year in its `years` text.
created_from says which file (or "years") gave the date."""
from __future__ import annotations

import os
import re
from datetime import datetime, timezone

from . import config
from .candidates import Projects
from .db import DB


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def run(db: DB) -> dict:
    P = Projects(db)
    mtime = {r["path"]: r["mtime"] for r in db.q("SELECT path, mtime FROM files")}

    def when(path: str):
        if path in mtime and mtime[path]:
            return mtime[path]
        try:
            return os.stat(path).st_mtime
        except OSError:
            return None

    best: dict = {}   # project id -> (timestamp, path)

    def offer(pid, path):
        t = when(path)
        if pid is not None and t and (pid not in best or t < best[pid][0]):
            best[pid] = (t, path)

    for r in db.q("SELECT DISTINCT project_id, source FROM pictures WHERE status IN ('kept','review','approved') "
                  "AND project_id IS NOT NULL"):
        offer(r["project_id"], r["source"])
    videos = 0
    for r in db.q("SELECT path FROM files WHERE kind='video'"):
        pid = P.named_by_folder(r["path"])
        if pid is not None:
            offer(pid, r["path"])
            videos += 1
    by_media = by_years = 0
    for pid, p in P.p.items():
        if pid in best:
            t, path = best[pid]
            db.x("UPDATE projects SET created_at=?, created_from=? WHERE id=?", (_day(t), os.path.relpath(path, config.ARCHIVE), pid))
            by_media += 1
        else:
            row = db.one("SELECT years FROM projects WHERE id=?", (pid,))
            ys = re.findall(r"\b(20[0-3]\d)\b", (row["years"] if row else "") or "")
            db.x("UPDATE projects SET created_at=?, created_from=? WHERE id=?",
                 (f"{min(ys)}-01-01" if ys else None, "years" if ys else None, pid))
            by_years += bool(ys)
    return {"from_pictures_and_videos": by_media, "videos_matched": videos, "from_years_only": by_years,
            "no_date": len(P.p) - by_media - by_years}
