"""One pass over the archive: find each picture, read it on this Mac, let JEV (and Claude when JEV is unsure and the picture is
safe to describe) decide what it is and which project it shows, and keep a covered copy of screens and diagrams.

The text read from a picture stays in memory for this pass only; the database keeps labels, sizes and counts, never the text.
Every picture looked at is recorded, so a later run continues where this one stopped."""
from __future__ import annotations

import hashlib
import os
import queue as queue_mod
import threading
import time
import traceback
import zipfile

from . import category, config, find, judge, privacy, vision
from .candidates import Projects
from .db import DB, now


def _record(db: DB, **f) -> None:
    f.setdefault("created_at", now())
    f["updated_at"] = now()
    cols = ",".join(f)
    db.x(f"INSERT OR IGNORE INTO pictures({cols}) VALUES({','.join('?' * len(f))})", tuple(f.values()))


def decide(state: str, options: dict, risky: bool, use_claude: bool, log, claude_state: str = "") -> tuple:
    """(kind, kind_p, project id or None, project_p, decided_by, status, reason)"""
    a = judge.ask_jev(state, options)
    by = "jev"
    sure_kind = a["kind_p"] >= config.JEV_SURE
    sure_project = a["project"] != judge.NONE and a["project_p"] >= config.JEV_SURE
    if sure_kind and a["kind"] not in config.KIND_KEEP:
        return a["kind"], a["kind_p"], None, 0.0, by, "skipped", f"not a screen or a diagram ({a['kind']})"
    if not (sure_kind and sure_project):   # JEV is unsure about the kind or the project
        if risky:
            return a["kind"], a["kind_p"], (int(a["project"]) if a["project"] != judge.NONE else None), a["project_p"], by, \
                "review", "JEV unsure and the picture shows signs of patient data: review it"
        if use_claude and options:
            try:
                a = judge.ask_claude(claude_state or state, options)
                by = "claude"
            except Exception as e:
                log(f"claude failed: {e}")
                return a["kind"], a["kind_p"], None, a["project_p"], by, "skipped", "JEV unsure and the second opinion failed"
        else:   # kept aside for a later pass (Claude in a batch, or the user)
            return a["kind"], a["kind_p"], (int(a["project"]) if a["project"] != judge.NONE else None), a["project_p"], by, \
                "unsure", "JEV unsure: left for a later pass"
    if a["kind"] not in config.KIND_KEEP:
        return a["kind"], a["kind_p"], None, 0.0, by, "skipped", f"not a screen or a diagram ({a['kind']})"
    if a["project"] == judge.NONE:
        return a["kind"], a["kind_p"], None, 0.0, by, "skipped", "project unknown"
    return a["kind"], a["kind_p"], int(a["project"]), a["project_p"], by, "kept", ""


def decide_picture(state: str, pid, project_p: float, pby: str, risky: bool, use_claude: bool, log, claude_state: str,
                   options: dict) -> tuple:
    """(kind, kind_p, project id or None, project_p, decided_by, status, reason). The project comes from the document; JEV says
    whether this picture is a screen or a diagram (keep_p = P(screen) + P(diagram))."""
    a = judge.ask_kind(state)
    keep_p, kind = a["keep_p"], a["kind"]
    by = "jev" if pby == "rule" else pby
    if keep_p <= 1 - config.JEV_SURE:
        return kind, 1 - keep_p, None, 0.0, by, "skipped", f"not a screen or a diagram ({kind})"
    project_ok = pid is not None and project_p >= config.PROJECT_SURE
    if keep_p >= config.KEEP_SURE and project_ok:
        return kind, keep_p, pid, project_p, by, "kept", ""
    if risky:
        return kind, keep_p, pid, project_p, by, "review", "JEV unsure and the picture shows signs of patient data: review it"
    if use_claude and options:
        try:
            c = judge.ask_claude(claude_state, options)
            if c["kind"] in config.KIND_KEEP and c["project"] != judge.NONE:
                return c["kind"], 1.0, int(c["project"]), 1.0, "claude", "kept", ""
            return c["kind"], 1.0, None, 0.0, "claude", "skipped", "second opinion: not a screen or a diagram, or project unknown"
        except Exception as e:
            log(f"claude failed: {e}")
    why = "JEV unsure it is a screen or a diagram" if keep_p < config.KEEP_SURE else "project unknown"
    return kind, keep_p, pid, project_p, by, "unsure", why + ": left for a later pass"


def run(db: DB | None = None, root: str = config.ARCHIVE, only: str = "", limit: int = 0, use_claude: bool = True, log=print,
        retry_unsure: bool = False, from_index: bool = False, min_priority: int = 0) -> dict:
    """from_index: go through the indexed files best first (vault index), only those at or above min_priority."""
    db = db or DB()
    retry_sources = set()
    if retry_unsure:   # pictures JEV was unsure about are looked at again (the text was never stored, so they are read again)
        retry_sources = {r["source"] for r in db.q("SELECT DISTINCT source FROM pictures WHERE status='unsure'")}
        db.x("DELETE FROM pictures WHERE status='unsure'")
        for src in retry_sources:
            db.x("UPDATE files SET status='pending' WHERE path=?", (src,))
    if not db.one("SELECT 1 FROM projects LIMIT 1"):
        raise RuntimeError("No projects yet: run `vault import-seed` (or add projects) first.")
    projects = Projects(db)
    rid = db.x("INSERT INTO runs(started_at,status,pid) VALUES(?,?,?)", (now(), "running", os.getpid())).lastrowid
    n_files = looked = kept = resumed = 0
    t0 = time.time()
    # documents a previous run went through are not opened again (rendering a PDF just to see it was done costs heat);
    # the last one may have been cut short, so it is read again
    last = db.one("SELECT source FROM pictures ORDER BY id DESC LIMIT 1")
    done_docs = {r["source"] for r in db.q("SELECT DISTINCT source FROM pictures")} - ({last["source"]} if last else set()) - retry_sources
    if from_index:
        queue = [r["path"] for r in db.q("SELECT path FROM files WHERE status='pending' AND priority>=? AND kind!='video' "
                                         "ORDER BY priority DESC, size DESC", (min_priority,))]
        db.x("UPDATE runs SET total=?, min_priority=? WHERE id=?", (len(queue), min_priority, rid))
        # the index already holds only files still to do (documents put back for another look included); pictures already
        # decided are skipped one by one by their hash, so no document is skipped as a whole here
        done_docs = set()
        log(f"{len(queue)} indexed files at priority {min_priority} or more, best first")
    else:
        queue = find.files(root)
    # Two workers that overlap: a reader takes pictures out of the files and reads their text (Vision), keeping up to BATCH of
    # them ready; when only REFILL are left it starts on the next ones. The main thread lets JEV decide, covers and saves. So
    # JEV never waits for Vision and pictures keep coming at an even pace. A file is marked done only once all its pictures
    # have been decided, so stopping half way loses nothing.
    BATCH, REFILL = 10, 5
    q: "queue_mod.Queue" = queue_mod.Queue()
    room = threading.Condition()
    stop = threading.Event()
    counts = {"files": 0, "looked": 0, "resumed": 0, "current": ""}

    def put(item):
        with room:
            while q.qsize() >= BATCH and not stop.is_set():
                room.wait_for(lambda: q.qsize() <= REFILL or stop.is_set(), timeout=1.0)
        q.put(item)

    def reader():
        import objc
        try:
            for path in queue:
                if stop.is_set():
                    break
                rel = os.path.relpath(path, root)
                if only and only.lower() not in rel.lower():
                    continue
                counts["files"] += 1
                counts["current"] = rel[:200]
                if path in done_docs:
                    counts["resumed"] += 1
                    continue
                with objc.autorelease_pool():
                    t_file = time.monotonic()
                    doc, folder = os.path.basename(path), os.path.dirname(rel)
                    options = {str(pid): projects.label(pid) for pid in projects.options(path, root)}
                    doc_risky = projects.risky_folder(path) or bool(db.one("SELECT 1 FROM sources WHERE path=? AND status='sensitive'", (path,)))
                    try:
                        pics = list(find.pictures(path))
                    except (OSError, zipfile.BadZipFile, KeyError, ValueError, RuntimeError):
                        pics = []
                    dtext = find.doc_text(path) if pics else ""
                    for member, data, img in pics:
                        if stop.is_set():
                            break
                        sha = hashlib.sha1(data if data is not None else f"{path}#{member}".encode()).hexdigest()
                        if db.one("SELECT 1 FROM pictures WHERE sha=?", (sha,)):
                            continue
                        counts["looked"] += 1
                        base = dict(sha=sha, source=path, member=member)
                        if img is None:
                            img = vision.from_bytes(data)
                            if img is None:
                                _record(db, **base, status="skipped", reason="picture could not be read")
                                continue
                        w, h = vision.size(img)
                        why = find.quality_ok(w, h)
                        if why:
                            _record(db, **base, width=w, height=h, status="skipped", reason=why, decided_by="rule")
                            continue
                        lines, faces = vision.read(img)
                        if len(lines) < config.MIN_LINES:
                            _record(db, **base, width=w, height=h, text_lines=len(lines), status="skipped", decided_by="rule",
                                    reason="little text: a logo, a photo or decoration")
                            continue
                        put(("pic", dict(base=base, img=img, w=w, h=h, lines=lines, faces=faces, doc=doc, folder=folder,
                                         options=options, doc_risky=doc_risky, path=path, dtext=dtext)))
                    put(("file_done", path))
                    if config.REST > 0:   # rest as long as the reading took (times REST), so the Mac stays cooler and quieter
                        time.sleep(min(30.0, (time.monotonic() - t_file) * config.REST))
                if limit and counts["looked"] >= limit:
                    break
        except Exception:
            log("reader failed: " + traceback.format_exc())
        finally:
            q.put(("end", None))

    doc_project: dict = {}

    def project_of(it) -> tuple:
        """(project id or None, probability, who decided) for a whole document: certain from the catalogue, else one JEV question
        over the document's own name, folder and text."""
        # the rules only gather the candidates; JEV always decides, reading the full path, the file name and the text
        if not it["options"]:
            return None, 0.0, "rule"
        state = judge.doc_state(it["doc"], it["folder"], it["dtext"], "\n".join(t for t, _ in it["lines"]))
        pid, pp = judge.ask_project(state, it["options"], named=[n for n in [projects.named_by_folder(it["path"])] if n])
        return pid, pp, "jev"

    th = threading.Thread(target=reader, daemon=True, name="reader")
    th.start()
    status = "done"
    try:
        last_tick = 0.0
        while True:
            kind, item = q.get()
            with room:
                room.notify_all()
            if kind == "end":
                break
            if kind == "file_done":
                db.x("UPDATE files SET status='done', done_at=? WHERE path=?", (now(), item))
            else:
                it = item
                lines, faces, base = it["lines"], it["faces"], it["base"]
                risky = it["doc_risky"] or any(privacy.PHI_TEXT.search(t) for t, _ in lines)
                state = judge.state_text(it["doc"], it["folder"], it["w"], it["h"], lines)   # JEV runs on this Mac: all the text
                # Claude gets the same picture description without any line that looks private (names, e-mail, IDs, dates...)
                claude_state = judge.state_text(it["doc"], it["folder"], it["w"], it["h"], [l for l in lines if not privacy.private(l[0], True)])
                if it["path"] not in doc_project:
                    doc_project[it["path"]] = project_of(it)
                pid, project_p, pby = doc_project[it["path"]]
                kind_, kind_p, pid, project_p, by, status_, reason = decide_picture(state, pid, project_p, pby, risky, use_claude, log,
                                                                                   claude_state, it["options"])
                rec = dict(**base, width=it["w"], height=it["h"], text_lines=len(lines), kind=kind_, kind_p=round(kind_p, 3), project_id=pid,
                           project_p=round(project_p, 3), decided_by=by, phi_risk=int(risky), status=status_, reason=reason)
                if status_ in ("kept", "review"):
                    cat = category.by_rule(base["source"], base["member"], it["w"], it["h"], kind_)
                    if cat:
                        rec.update(category=cat, category_by="rule")
                    else:
                        cat, cp = category.ask(state)
                        rec.update(category=cat, category_by=f"jev {round(cp * 100)}%")
                    if config.REDACT:
                        out, bx, clean = privacy.covered(it["img"], privacy.boxes(lines, faces, risky), risky)
                    else:   # kept as it is; the owner picks the pictures to cover later
                        out, bx, clean = it["img"], [], True
                    rec["redactions"] = len(bx)
                    if clean:
                        rec["file"] = os.path.join(config.PICTURES, base["sha"][:2], base["sha"] + ".png")
                        vision.save(out, rec["file"])
                        try:   # drawn-on screenshots (bug reports) are hidden in the folder view by default
                            from .annotations import score as annot
                            a = annot(rec["file"])
                            if a:
                                rec.update(annotated=int(a["annotated"]), annotated_score=f"{a['lines']}/{a['thin']}", annotated_by="rule")
                        except Exception:
                            pass
                        kept += 1
                    else:
                        rec.update(status="skipped", reason="private text left after 4 covering passes: dropped")
                _record(db, **rec)
            if time.monotonic() - last_tick > 3:
                last_tick = time.monotonic()
                db.x("UPDATE runs SET files=?,looked=?,kept=?,current=?,resumed=? WHERE id=?",
                     (counts["files"], counts["looked"], kept, counts["current"], counts["resumed"], rid))
    except KeyboardInterrupt:
        status = "stopped"
    except Exception:
        stop.set()
        db.x("UPDATE runs SET status='error',error=?,finished_at=? WHERE id=?", (traceback.format_exc()[-800:], now(), rid))
        raise
    finally:
        stop.set()
        with room:
            room.notify_all()
    n_files, looked = counts["files"], counts["looked"]
    db.x("UPDATE runs SET status=?,finished_at=?,files=?,looked=?,kept=?,current='' WHERE id=?", (status, now(), n_files, looked, kept, rid))
    return {"files": n_files, "pictures_looked": looked, "kept": kept, "minutes": round((time.time() - t0) / 60, 1)}
