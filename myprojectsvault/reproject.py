"""vault reproject: ask JEV again which project each document with kept pictures belongs to, reading the full path, the file
name and the document's text (the rules only gather the candidates). A document moves only when JEV is sure; every move is
written to reproject-<time>.tsv in the data folder so it can be checked."""
from __future__ import annotations

import os
import time

from . import config, find, judge, vision
from .candidates import Projects
from .db import DB, now


def run(db: DB, only: str = "", log=print, dry: bool = False) -> dict:
    P = Projects(db)
    docs = db.q("SELECT source, project_id, COUNT(*) n FROM pictures WHERE status IN ('kept','review','approved') "
                "AND project_id IS NOT NULL GROUP BY source, project_id ORDER BY source")
    docs = [d for d in docs if only.lower() in d["source"].lower()]
    out = os.path.join(config.DATA, f"reproject-{time.strftime('%Y%m%d-%H%M')}.tsv")
    moved = same = unsure = 0
    with open(out, "w", encoding="utf-8") as f:
        f.write("pictures\tfrom\tto\tJEV sure\tdocument\n")
        for i, d in enumerate(docs, 1):
            src, cur = d["source"], d["project_id"]
            ids = list(dict.fromkeys([cur] + P.options(src, config.ARCHIVE)))
            opts = {str(p): P.label(p) for p in ids if p in P.p}
            rel = os.path.relpath(src, config.ARCHIVE)
            try:
                text = find.doc_text(src) if os.path.exists(src) else ""
            except Exception:
                text = ""
            ptext = ""
            if not text:   # an image file: the text read from its saved copy stands in for the document's text
                r = db.one("SELECT file FROM pictures WHERE source=? AND file!='' LIMIT 1", (src,))
                try:
                    with open(r["file"], "rb") as fh:
                        ptext = "\n".join(t for t, _ in vision.read(vision.from_bytes(fh.read()))[0])
                except Exception:
                    ptext = ""
            state = judge.doc_state(os.path.basename(src), os.path.dirname(rel), text, ptext)
            pid, pp = judge.ask_project(state, opts, current=str(cur), named=[n for n in [P.named_by_folder(src)] if n])
            if pid is None or pid == cur:
                same += 1
            elif pp < config.PROJECT_SURE:
                unsure += 1
            else:
                if not dry:
                    db.x("UPDATE pictures SET project_id=?, project_p=?, updated_at=? WHERE source=? AND project_id=? "
                         "AND status IN ('kept','review','approved')", (pid, round(pp, 3), now(), src, cur))
                f.write(f"{d['n']}\t{P.p[cur]['name'] if cur in P.p else cur}\t{P.p[pid]['name']}\t{round(pp * 100)}%\t{rel}\n")
                moved += 1
            if i % 100 == 0:
                log(f"{i}/{len(docs)}")
    return {"documents": len(docs), "moved": moved, "kept_same": same, "jev_unsure_left_as_is": unsure, "log": out}
