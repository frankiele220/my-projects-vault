"""vault import-seed | run [--only TEXT] [--limit N] [--no-claude] | stats"""
from __future__ import annotations

import argparse
import json

from . import config
from .db import DB, import_seed


def main():
    ap = argparse.ArgumentParser(prog="vault", description="My Projects Vault: portfolio database and picture library")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("import-seed", help="copy the projects of an older catalogue once (read only)")
    r = sub.add_parser("run", help="find, read, classify and keep pictures")
    r.add_argument("--root", default=config.ARCHIVE)
    r.add_argument("--only", default="", help="only files whose path contains this text")
    r.add_argument("--limit", type=int, default=0, help="stop after this many new pictures")
    r.add_argument("--no-claude", action="store_true", help="JEV only; unsure pictures are marked 'unsure' for a later pass")
    r.add_argument("--retry-unsure", action="store_true", help="look again at the pictures JEV was unsure about")
    r.add_argument("--from-index", action="store_true", help="go through the indexed files best first (run `vault index` before)")
    r.add_argument("--min-priority", type=int, default=50, help="with --from-index: only files at this priority or more (0..100)")
    ix = sub.add_parser("index", help="list every image, video and picture-holding document with a priority (fast, nothing is read)")
    ix.add_argument("--root", default=config.ARCHIVE)
    sub.add_parser("stats")
    dt = sub.add_parser("details", help="write each project's details (problems, solution, users, country, years) via OpenRouter")
    dt.add_argument("--only", default="", help="only projects whose name contains this text")
    dt.add_argument("--redo", action="store_true", help="write again projects that already have details")
    dt.add_argument("--limit", type=int, default=0)
    dt.add_argument("--show", action="store_true", help="print what would be sent for --only, send nothing")
    an = sub.add_parser("annotations", help="find pictures with annotations drawn on them (bug-report screenshots)")
    an.add_argument("--redo", action="store_true", help="score every kept picture again (the owner's own choices stay)")
    pb = sub.add_parser("publish", help="build the portfolio page (site folder) and the PDF from the export")
    pb.add_argument("--site", default="", help="the site's portfolio folder, e.g. ~/my-site/portfolio")
    pb.add_argument("--pdf", default="", help="where to write the PDF")
    pb.add_argument("--only", default="", help="only projects whose name contains this text (preview)")
    ex = sub.add_parser("export", help="the portfolio as JSON for the website and the PDF (sharing rules applied)")
    ex.add_argument("--out", default="", help="write the JSON to this file")
    ex.add_argument("--with-pictures", action="store_true", help="only projects that have shareable pictures")
    sub.add_parser("dates", help="set each project's created_at to the earliest date of its pictures and videos")
    rp = sub.add_parser("reproject", help="ask JEV again which project each document with kept pictures belongs to (full path)")
    rp.add_argument("--only", default="", help="only documents whose path contains this text")
    rp.add_argument("--dry", action="store_true", help="only write the list of moves, change nothing")
    sub.add_parser("categorize", help="give kept pictures a category: website, web app, mobile app, admin, store assets, diagram")
    sub.add_parser("consolidate", help="fold catalogue entries into portfolio projects (data/portfolio-groups.json)")
    sub.add_parser("uncover", help="put the original back for pictures saved with covered areas (read again from the archive)")
    sub.add_parser("monitor", help="a page on this Mac that shows the run's progress (http://127.0.0.1:8791)")
    a = ap.parse_args()
    if a.cmd in ("run", "reproject", "categorize"):   # projects' created_at follows their pictures
        import atexit
        atexit.register(lambda: __import__("myprojectsvault.dates", fromlist=["run"]).run(DB()))
    if a.cmd == "monitor":
        from .monitor import serve
        return serve()
    db = DB()
    if a.cmd == "import-seed":
        print(json.dumps(import_seed(db), ensure_ascii=False))
    elif a.cmd == "run":
        from .pipeline import run
        print(json.dumps(run(db, a.root, a.only, a.limit, not a.no_claude, retry_unsure=a.retry_unsure,
                             from_index=a.from_index, min_priority=a.min_priority), ensure_ascii=False))
    elif a.cmd == "index":
        from .index import build
        print(json.dumps(build(db, a.root), ensure_ascii=False, indent=1))
    elif a.cmd == "details" and a.show:
        from .details import material
        for r in db.q("SELECT id, name FROM projects WHERE merged_into IS NULL AND status!='hidden' AND name LIKE ?", (f"%{a.only}%",)):
            text, used = material(db, r["id"])
            print(f"===== {r['name']}  ({len(text)} chars, {len(used)} documents)\n{text}\n")
    elif a.cmd == "details":
        from .details import run as details
        print(json.dumps(details(db, a.only, a.redo, a.limit), ensure_ascii=False))
    elif a.cmd == "annotations":
        from .annotations import backfill
        print(json.dumps(backfill(db, a.redo), ensure_ascii=False))
    elif a.cmd == "publish":
        from .publish import run as publish
        print(json.dumps(publish(db, a.site, a.pdf, a.only), ensure_ascii=False))
    elif a.cmd == "export":
        from .export import run as export
        print(json.dumps(export(db, a.out, a.with_pictures), ensure_ascii=False))
    elif a.cmd == "dates":
        from .dates import run as dates
        print(json.dumps(dates(db), ensure_ascii=False))
    elif a.cmd == "reproject":
        from .reproject import run as reproject
        print(json.dumps(reproject(db, a.only, dry=a.dry), ensure_ascii=False))
    elif a.cmd == "categorize":
        from .category import backfill
        print(json.dumps(backfill(db), ensure_ascii=False))
    elif a.cmd == "consolidate":
        from .consolidate import run as consolidate
        print(json.dumps(consolidate(db), ensure_ascii=False))
    elif a.cmd == "uncover":
        from .uncover import run as uncover
        print(json.dumps(uncover(db), ensure_ascii=False))
    elif a.cmd == "stats":
        out = {"projects": db.one("SELECT COUNT(*) c FROM projects")["c"],
               "pictures": {f"{r['status']}/{r['decided_by']}": r["c"] for r in
                            db.q("SELECT status, decided_by, COUNT(*) c FROM pictures GROUP BY status, decided_by")},
               "kinds_kept": {r["kind"]: r["c"] for r in db.q("SELECT kind, COUNT(*) c FROM pictures WHERE status IN ('kept','review') GROUP BY kind")},
               "projects_with_pictures": db.one("SELECT COUNT(DISTINCT project_id) c FROM pictures WHERE status IN ('kept','approved') AND project_id IS NOT NULL")["c"],
               "last_run": dict(db.one("SELECT * FROM runs ORDER BY id DESC LIMIT 1") or {})}
        print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
