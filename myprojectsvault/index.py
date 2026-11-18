"""Step 0, fast: list every image, video and picture-holding document of the archive, with a priority, before anything is read.

Nothing is decoded and no text is read: image and video files are listed by name and size, and a Word, PowerPoint or Excel file
is opened only far enough to count the pictures inside it (its table of contents). The run then goes through the files best
first: screenshots and design documents before everything else, so pictures start landing in their projects straight away."""
from __future__ import annotations

import os
import re
import zipfile

from . import config, privacy
from .candidates import Projects
from .db import DB, now
from .find import MEDIA

IMAGE = {"png", "jpg", "jpeg", "heic", "webp"}
VIDEO = {"mp4", "mov", "m4v", "avi", "mkv", "webm"}
DOCS = {"docx", "pptx", "xlsx", "pdf"}
SCREENISH = re.compile(r"screen ?shot|screenshot|screen|capture|snap|mock ?up|wire ?frame|\bui\b|\bux\b|dashboard|prototype|"
                       r"layout|design|figma|sketch|portal|admin|app|page|flow|diagram|architecture|sitemap|demo", re.I)
DOC_GOOD = re.compile(r"design|demo|release|feedback|spec|requirement|user guide|guide|manual|walkthrough|uat|screens?|"
                      r"proposal|presentation|deck|overview|architecture|flow|prototype|wireframe|mock", re.I)


def kind_of(name: str) -> str:
    ext = name.lower().rsplit(".", 1)[-1] if "." in name else ""
    return "image" if ext in IMAGE else "video" if ext in VIDEO else ext if ext in DOCS else ""


def score(path: str, rel: str, kind: str, size: int, media: int, in_project: bool) -> tuple:
    """(priority 0..100, why) for one file."""
    name = os.path.basename(path)
    pts, why = 0, []
    if kind == "image":
        pts += 45
        why.append("image")
        if name.lower().endswith(".png"):
            pts += 10
            why.append("png")
        if SCREENISH.search(name):
            pts += 25
            why.append("named like a screenshot")
        if not 60_000 <= size <= 12_000_000:
            pts -= 25
            why.append("unusual size")
    elif kind in ("pptx", "docx", "xlsx"):
        if media == 0:
            return 0, "no pictures inside"
        pts += 20 + min(25, media * 2)
        why.append(f"{media} pictures inside")
        if DOC_GOOD.search(name):
            pts += 15
            why.append("design / demo / release document")
    elif kind == "pdf":
        pts += 10
        why.append("pdf")
    if in_project:
        pts += 15
        why.append("inside a project folder")
    return max(0, min(100, pts)), ", ".join(why)


def build(db: DB | None = None, root: str = config.ARCHIVE, log=print) -> dict:
    db = db or DB()
    projects = Projects(db) if db.one("SELECT 1 FROM projects LIMIT 1") else None
    done_sources = {r["source"] for r in db.q("SELECT DISTINCT source FROM pictures")}
    n = {"image": 0, "video": 0, "doc": 0, "skipped": 0}
    rows = []
    for d, dirs, names in os.walk(root, onerror=lambda e: None):
        rel_d = os.path.relpath(d, root)
        dirs[:] = sorted(x for x in dirs if not x.startswith(".") and not privacy.JUNK_DIR.search("/" + os.path.join(rel_d, x) + "/"))
        for f in names:
            kind = kind_of(f)
            if not kind or f.startswith(("._", "~$")):
                continue
            path = os.path.join(d, f)
            rel = os.path.normpath(os.path.join(rel_d, f))
            if privacy.SENSITIVE_PATH.search(rel) or privacy.MEETING.search(rel):
                n["skipped"] += 1
                continue
            try:
                st = os.stat(path)
            except OSError:
                continue
            media = 0
            if kind in ("pptx", "docx", "xlsx"):
                try:
                    with zipfile.ZipFile(path) as z:
                        media = sum(1 for i in z.infolist() if MEDIA.match(i.filename) and i.file_size >= config.MIN_BYTES)
                except (zipfile.BadZipFile, OSError, KeyError):
                    continue
            in_project = bool(projects and projects.for_file(path, root))
            pri, why = score(path, rel, kind, st.st_size, media, in_project)
            status = "video" if kind == "video" else ("done" if path in done_sources else "pending")
            rows.append((path, kind, st.st_size, st.st_mtime, media, 0 if kind == "video" else pri, why, status))
            n["video" if kind == "video" else "image" if kind == "image" else "doc"] += 1
            if len(rows) >= 500:
                _flush(db, rows)
                log(f"indexed {sum(n.values())} files…")
    _flush(db, rows)
    pend = db.q("SELECT priority FROM files WHERE status='pending'")
    n["pending_by_priority"] = {b: sum(1 for r in pend if lo <= r["priority"] < hi)
                                for b, lo, hi in (("80-100", 80, 101), ("60-79", 60, 80), ("40-59", 40, 60), ("1-39", 1, 40), ("0", 0, 1))}
    return n


def _flush(db: DB, rows: list) -> None:
    if not rows:
        return
    db.conn.executemany("INSERT INTO files(path,kind,size,mtime,media,priority,why,status) VALUES(?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(path) DO UPDATE SET size=excluded.size,mtime=excluded.mtime,media=excluded.media,"
                        "priority=excluded.priority,why=excluded.why,status=CASE WHEN files.status='done' THEN 'done' ELSE excluded.status END",
                        rows)
    db.conn.commit()
    rows.clear()
