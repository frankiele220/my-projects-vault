"""vault monitor: a page on this Mac (127.0.0.1 only) that shows how the archive run is going, refreshing itself, with a button to
pause and resume the run (the run keeps its place; pausing only stops it from using the processor)."""
from __future__ import annotations

import json
import os
import re
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config, find
from .db import DB

PORT = int(os.environ.get("VAULT_MONITOR_PORT", "8791"))
_total = {"files": 0, "counting": True}


def _count_files():
    n = 0
    try:
        for _ in find.files(config.ARCHIVE):
            n += 1
            if n % 500 == 0:
                _total["files"] = n
    except Exception:
        pass
    _total.update(files=n, counting=False)


def _alive(pid) -> bool:
    try:
        os.kill(int(pid or 0), 0)
        return bool(pid)
    except (OSError, ValueError):
        return False


def _paused(pid) -> bool:
    """macOS: a stopped process shows state T in ps."""
    try:
        return os.popen(f"ps -o state= -p {int(pid)}").read().strip().startswith("T")
    except Exception:
        return False


def state(db: DB) -> dict:
    r = db.one("SELECT * FROM runs ORDER BY id DESC LIMIT 1")
    run = dict(r) if r else {}
    alive = _alive(run.get("pid")) and run.get("status") == "running"
    queued = run.get("total") or 0   # a run from the index knows its own length; a folder walk uses the archive count
    total = queued or _total["files"]
    out = {"run": run, "alive": alive, "paused": alive and _paused(run.get("pid")), "total_files": total,
           "counting": False if queued else _total["counting"], "from_index": bool(queued)}
    try:   # the page's version: an open tab reloads itself when the page file has changed
        out["page"] = int(os.path.getmtime(PAGE_FILE))
    except OSError:
        out["page"] = 0
    try:
        out["videos"] = db.one("SELECT COUNT(*) c FROM files WHERE kind='video'")["c"]
    except Exception:
        out["videos"] = 0
    if queued and run.get("min_priority") is not None:
        # progress of the whole job, not of this one run: a stop and a new run carry on from the same place
        j = db.one("SELECT SUM(status='done') d, SUM(status IN ('done','pending')) t FROM files WHERE kind!='video' AND priority>=?",
                   (run["min_priority"],))
        out["job"] = {"done": j["d"] or 0, "total": j["t"] or 0, "min_priority": run["min_priority"]}
    if run.get("started_at"):
        from datetime import datetime, timezone
        mins = (datetime.now(timezone.utc) - datetime.fromisoformat(run["started_at"])).total_seconds() / 60
        out["minutes"] = round(mins, 1)
        # documents already done by an earlier run are skipped in seconds: leave them out of the pace
        prev = db.one("SELECT files FROM runs WHERE id<? ORDER BY id DESC LIMIT 1", (run["id"],))
        resumed = 0 if queued else max(run.get("resumed") or 0, min(prev["files"] if prev else 0, run.get("files") or 0))
        rate = ((run.get("files") or 0) - resumed) / mins if mins > 0.5 else 0
        out["files_per_min"] = round(rate, 1)
        left = out["job"]["total"] - out["job"]["done"] if out.get("job") else max(0, total - (run.get("files") or 0))
        out["eta_min"] = round(left / rate) if rate and not out["counting"] else None
    out["status"] = {x["status"]: x["c"] for x in db.q("SELECT status, COUNT(*) c FROM pictures GROUP BY status")}
    out["by"] = {x["decided_by"] or "?": x["c"] for x in db.q("SELECT decided_by, COUNT(*) c FROM pictures GROUP BY decided_by")}
    out["kinds"] = {x["kind"]: x["c"] for x in db.q("SELECT kind, COUNT(*) c FROM pictures WHERE status IN ('kept','review','approved') GROUP BY kind")}
    out["reasons"] = [dict(x) for x in db.q("SELECT reason, COUNT(*) c FROM pictures WHERE status='skipped' GROUP BY reason ORDER BY c DESC LIMIT 6")]
    out["projects"] = [dict(x) for x in db.q(
        "SELECT pr.id, pr.name, COUNT(*) c FROM pictures p JOIN projects pr ON pr.id=p.project_id "
        "WHERE p.status IN ('kept','approved') GROUP BY pr.id ORDER BY c DESC LIMIT 12")]
    out["recent"] = [dict(x) for x in db.q(
        "SELECT p.id, p.status, p.kind, p.phi_risk, p.width, p.height, pr.name project FROM pictures p LEFT JOIN projects pr "
        "ON pr.id=p.project_id WHERE p.status IN ('kept','review') AND p.file!='' ORDER BY p.id DESC LIMIT 12")]
    return out


KEEP = "('kept','review','approved')"


def graph(db: DB) -> dict:
    """Every project that has pictures (the graph's nodes), the bins, and the newest picture id (where the event feed starts)."""
    nodes = [dict(r) for r in db.q(
        f"SELECT pr.id, pr.name, pr.industry, COUNT(*) c, SUM(p.status='review') review FROM pictures p JOIN projects pr "
        f"ON pr.id=p.project_id WHERE p.status IN {KEEP} GROUP BY pr.id ORDER BY c DESC")]
    bins = {r["status"]: r["c"] for r in db.q("SELECT status, COUNT(*) c FROM pictures WHERE status IN ('skipped','unsure') "
                                               "OR (status='review' AND project_id IS NULL) GROUP BY status")}
    for n in nodes:   # the newest pictures of each project fill the folder card's slots
        n["recent"] = [r["id"] for r in db.q(f"SELECT id FROM pictures WHERE project_id=? AND status IN {KEEP} AND file!='' "
                                             f"ORDER BY id DESC LIMIT 6", (n["id"],))]
    last = db.one("SELECT MAX(id) m FROM pictures")["m"] or 0
    return {"nodes": nodes, "bins": bins, "last": last}


def thumbs(db: DB, n: int = 90) -> list:
    """Covered pictures for the floating archive on the left (a random sample)."""
    return [r["id"] for r in db.q(f"SELECT id FROM pictures WHERE status IN {KEEP} AND file!='' ORDER BY RANDOM() LIMIT ?", (n,))]


def events(db: DB, after: int) -> list:
    """Pictures recorded after `after`, oldest first: what the page animates."""
    return [dict(r) for r in db.q(
        "SELECT p.id, p.status, p.kind, p.phi_risk, p.project_id, pr.name project, pr.industry, (p.file!='') has_file "
        "FROM pictures p LEFT JOIN projects pr ON pr.id=p.project_id WHERE p.id>? ORDER BY p.id LIMIT 300", (after,))]


def where_from(member: str) -> str:
    """Where in its document a picture sits, in plain words."""
    if not member:
        return "the file itself"
    if member.startswith("page "):
        return "PDF " + member
    return "picture inside the file: " + member


def project(db: DB, pid: int) -> dict:
    """One project's pictures with where each came from (for the folder view)."""
    p = db.one("SELECT id, name, industry, created_at, created_from, client_name, client_country, years, summary, problem, solution, results, tech, loc_role, details, details_at, details_model, portfolio, tech_override FROM projects WHERE id=?", (pid,))
    if not p:
        return {}
    pics = []
    for r in db.q(f"SELECT id, source, member, kind, kind_p, project_p, status, phi_risk, redactions, width, height, file, "
                  f"decided_by, created_at, category, category_by, public, annotated, annotated_by FROM pictures WHERE project_id=? AND status IN {KEEP} AND file!='' ORDER BY source, id",
                  (pid,)):
        d = dict(r)
        d["where"] = where_from(d["member"])
        d["folder"] = os.path.relpath(os.path.dirname(d["source"]), config.ARCHIVE) if d["source"].startswith(config.ARCHIVE) else os.path.dirname(d["source"])
        d["doc"] = os.path.basename(d["source"])
        pics.append(d)
    from .roles import effective
    p = dict(p)
    if p.get("tech_override"):   # the owner's own tech stack replaces the written one
        try:
            d = json.loads(p["details"] or "{}")
            d["tech"] = json.loads(p["tech_override"])
            p["details"] = json.dumps(d, ensure_ascii=False)
        except ValueError:
            pass
    role = effective(p["details"], p["tech"], p["portfolio"], p["name"])
    return {**dict(p), "pictures": pics, "archive": config.ARCHIVE, "role": role}


THUMBS = os.path.join(config.PICTURES, "_thumbs")
THUMB_PX = 360


def thumb(pid: int) -> str:
    """Path of a JPEG at most THUMB_PX wide of a kept picture (the covered copy), made with macOS ImageIO the first time."""
    r = DB().one("SELECT sha, file FROM pictures WHERE id=? AND status IN ('kept','review','approved')", (pid,))
    src = os.path.realpath(r["file"]) if r and r["file"] else ""
    if not (src.startswith(os.path.realpath(config.PICTURES) + os.sep) and os.path.isfile(src)):
        return ""
    out = os.path.join(THUMBS, r["sha"] + ".jpg")
    if os.path.isfile(out):
        return out
    import Quartz
    from Foundation import NSURL
    s = Quartz.CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(src), None)
    img = Quartz.CGImageSourceCreateThumbnailAtIndex(s, 0, {Quartz.kCGImageSourceCreateThumbnailFromImageAlways: True,
                                                         Quartz.kCGImageSourceThumbnailMaxPixelSize: THUMB_PX,
                                                         Quartz.kCGImageSourceCreateThumbnailWithTransform: True}) if s else None
    if img is None:
        return src
    os.makedirs(THUMBS, exist_ok=True)
    tmp = out + ".tmp"
    dest = Quartz.CGImageDestinationCreateWithURL(NSURL.fileURLWithPath_(tmp), "public.jpeg", 1, None)
    Quartz.CGImageDestinationAddImage(dest, img, {Quartz.kCGImageDestinationLossyCompressionQuality: 0.82})
    if not Quartz.CGImageDestinationFinalize(dest):
        return src
    os.replace(tmp, out)
    return out


def serve(port: int = PORT) -> None:
    threading.Thread(target=_count_files, daemon=True).start()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.headers.get("Host", "").split(":")[0] not in ("127.0.0.1", "localhost"):
                return self._send(403, {"error": "host"})
            if self.path == "/":
                with open(PAGE_FILE, "rb") as fh:
                    return self._send(200, fh.read(), "text/html; charset=utf-8")
            if self.path == "/api/state":
                return self._send(200, state(DB()))
            if self.path == "/api/all":   # every portfolio project, also those without pictures
                db = DB()
                rows = []
                for r in db.q("SELECT pr.id, pr.name, pr.industry, pr.details, (SELECT COUNT(*) FROM pictures p WHERE p.project_id=pr.id "
                              f"AND p.status IN {KEEP}) c FROM projects pr WHERE pr.merged_into IS NULL AND pr.status!='hidden'"):
                    d = {}
                    try:
                        d = json.loads(r["details"] or "{}")
                    except ValueError:
                        pass
                    rows.append({"id": r["id"], "name": r["name"], "industry": r["industry"], "c": r["c"],
                                 "notes_only": not r["c"] and not d.get("documents")})
                return self._send(200, rows)
            if self.path == "/api/graph":
                return self._send(200, graph(DB()))
            if self.path == "/api/thumbs":
                return self._send(200, thumbs(DB()))
            if self.path == "/api/projects":   # portfolio projects for the "move to project" picker
                return self._send(200, [dict(r) for r in DB().q("SELECT id, name FROM projects WHERE merged_into IS NULL "
                                                                "AND status!='hidden' ORDER BY name COLLATE NOCASE")])
            m = re.fullmatch(r"/api/project/(\d+)", self.path)
            if m:
                return self._send(200, project(DB(), int(m.group(1))))
            m = re.fullmatch(r"/api/events\?after=(\d+)", self.path)
            if m:
                return self._send(200, events(DB(), int(m.group(1))))
            m = re.fullmatch(r"/thumb/(\d+)", self.path)
            if m:   # a small copy (made once, then cached): the page shows pictures at a few dozen pixels
                t = thumb(int(m.group(1)))
                if t:
                    with open(t, "rb") as fh:
                        return self._send(200, fh.read(), "image/jpeg")
                return self._send(404, {"error": "not found"})
            m = re.fullmatch(r"/pic/(\d+)", self.path)
            if m:
                r = DB().one("SELECT file FROM pictures WHERE id=? AND status IN ('kept','review','approved')", (int(m.group(1)),))
                f = os.path.realpath(r["file"]) if r and r["file"] else ""
                if f.startswith(os.path.realpath(config.PICTURES) + os.sep) and os.path.isfile(f):
                    with open(f, "rb") as fh:
                        return self._send(200, fh.read(), "image/png")
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            if self.headers.get("Host", "").split(":")[0] not in ("127.0.0.1", "localhost") or \
                    self.headers.get("Origin") not in (f"http://127.0.0.1:{port}", f"http://localhost:{port}"):
                return self._send(403, {"error": "origin"})
            m = re.fullmatch(r"/api/portfolio/(\d+)/(auto|1|0)", self.path)
            if m:   # the owner's choice for one project: in the portfolio (1), left out (0), or follow the rule (auto)
                v = None if m.group(2) == "auto" else int(m.group(2))
                DB().x("UPDATE projects SET portfolio=? WHERE id=?", (v, int(m.group(1))))
                return self._send(200, {"ok": True})
            m = re.fullmatch(r"/api/annotated/(\d+)/([01])", self.path)
            if m:   # the owner corrects the drawn-on flag of one picture
                DB().x("UPDATE pictures SET annotated=?, annotated_by='owner' WHERE id=?", (int(m.group(2)), int(m.group(1))))
                return self._send(200, {"ok": True})
            m = re.fullmatch(r"/api/public/(\d+)/([01])", self.path)
            if m:   # the owner marks a picture "Can use in public"; every other picture stays private
                on = int(m.group(2))
                DB().x("UPDATE pictures SET public=?, public_at=CASE WHEN ?=1 THEN datetime('now') ELSE NULL END WHERE id=?",
                       (on, on, int(m.group(1))))
                return self._send(200, {"ok": True, "public": on})
            m = re.fullmatch(r"/api/move/(\d+)/(\d+)", self.path)
            if m:   # the owner moves a picture to another project by hand
                db = DB()
                if not db.one("SELECT 1 FROM projects WHERE id=? AND merged_into IS NULL", (int(m.group(2)),)):
                    return self._send(404, {"error": "no such project"})
                db.x("UPDATE pictures SET project_id=?, project_p=1.0, decided_by='owner', updated_at=datetime('now') WHERE id=?",
                     (int(m.group(2)), int(m.group(1))))
                return self._send(200, {"ok": True})
            m = re.fullmatch(r"/api/category/(\d+)/(website|webapp|mobile|admin|store|diagram)", self.path)
            if m:   # the owner moves a picture to another group by hand
                DB().x("UPDATE pictures SET category=?, category_by='owner' WHERE id=?", (m.group(2), int(m.group(1))))
                return self._send(200, {"ok": True})
            m = re.fullmatch(r"/api/reveal/(\d+)/(doc|copy)", self.path)
            if m:   # show the source document (or the saved covered copy) selected in Finder
                r = DB().one(f"SELECT source, file FROM pictures WHERE id=? AND status IN {KEEP}", (int(m.group(1)),))
                f = (r["source"] if m.group(2) == "doc" else r["file"]) if r else ""
                if not (f and os.path.exists(f)):
                    return self._send(404, {"error": "File not found (is the archive drive connected?)"})
                import subprocess
                subprocess.Popen(["open", "-R", f])
                return self._send(200, {"ok": True})
            r = DB().one("SELECT pid, status FROM runs ORDER BY id DESC LIMIT 1")
            pid = r["pid"] if r and r["status"] == "running" else 0
            if not _alive(pid):
                return self._send(409, {"error": "No run is in progress"})
            if self.path == "/api/pause":
                os.kill(pid, signal.SIGSTOP)
            elif self.path == "/api/resume":
                os.kill(pid, signal.SIGCONT)
            else:
                return self._send(404, {"error": "not found"})
            return self._send(200, {"ok": True})

    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    print(f"My Projects Vault monitor: http://127.0.0.1:{port}")
    srv.serve_forever()


PAGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor.html")   # read on every load: edit and refresh
