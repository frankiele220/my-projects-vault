"""The portfolio database (SQLite). Other tools read it; only My Projects Vault writes it."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  client_type TEXT DEFAULT '', client_name TEXT DEFAULT '', client_country TEXT DEFAULT '', industry TEXT DEFAULT '',
  years TEXT DEFAULT '', started TEXT DEFAULT '', ended TEXT DEFAULT '', loc_role TEXT DEFAULT '', team TEXT DEFAULT '',
  summary TEXT DEFAULT '', problem TEXT DEFAULT '', solution TEXT DEFAULT '', results TEXT DEFAULT '',
  tech TEXT DEFAULT '[]', domains TEXT DEFAULT '[]', evidence TEXT DEFAULT '[]', summary_vi TEXT DEFAULT '',
  confidence REAL DEFAULT 0.5, can_name_client INTEGER DEFAULT 0,
  status TEXT DEFAULT 'draft',            -- draft / approved (kept) / hidden
  units TEXT DEFAULT '[]', created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS units (           -- a folder (or a batch of loose files) of the archive
  id INTEGER PRIMARY KEY, root TEXT NOT NULL, path TEXT NOT NULL UNIQUE, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sources (         -- documents of a unit that were read, and whether they showed patient data
  id INTEGER PRIMARY KEY, unit_id INTEGER, path TEXT NOT NULL UNIQUE, status TEXT);
CREATE TABLE IF NOT EXISTS pictures (        -- every picture looked at, kept or not, so a later run skips it
  id INTEGER PRIMARY KEY, sha TEXT NOT NULL UNIQUE, source TEXT NOT NULL, member TEXT DEFAULT '',
  width INTEGER, height INTEGER, text_lines INTEGER DEFAULT 0,
  kind TEXT DEFAULT '', kind_p REAL DEFAULT 0,          -- screen / diagram / slide / document / asset / photo
  project_id INTEGER, project_p REAL DEFAULT 0,          -- NULL: no project
  decided_by TEXT DEFAULT '',                            -- rule / jev / claude / user
  phi_risk INTEGER DEFAULT 0, redactions INTEGER DEFAULT 0,
  file TEXT DEFAULT '',                                  -- the covered copy (the only copy kept), '' when not kept
  status TEXT DEFAULT '',     -- kept (waits for the user) / approved / rejected / skipped / review (unsure, patient data: the user decides)
  reason TEXT DEFAULT '', created_at TEXT, updated_at TEXT);
CREATE INDEX IF NOT EXISTS pictures_project ON pictures(project_id, status);
CREATE TABLE IF NOT EXISTS files (           -- the index: every picture, video and picture-holding document of the archive
  id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE, kind TEXT,           -- image / video / docx / pptx / xlsx / pdf
  size INTEGER, mtime REAL, media INTEGER DEFAULT 0,                       -- pictures inside a document (counted, not opened)
  priority INTEGER DEFAULT 0, why TEXT DEFAULT '',                          -- how likely it holds screens or diagrams
  status TEXT DEFAULT 'pending', done_at TEXT);                             -- pending / done / video (indexed only)
CREATE INDEX IF NOT EXISTS files_queue ON files(status, priority DESC);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY, started_at TEXT, finished_at TEXT, status TEXT, files INTEGER DEFAULT 0, looked INTEGER DEFAULT 0,
  kept INTEGER DEFAULT 0, current TEXT DEFAULT '', error TEXT DEFAULT '', pid INTEGER);
"""

_lock = threading.RLock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: str = config.DB_PATH):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(runs)")]
        if "resumed" not in cols:   # documents a run skipped as already done
            self.conn.execute("ALTER TABLE runs ADD COLUMN resumed INTEGER DEFAULT 0")
        if "total" not in cols:     # how many files this run means to go through (from the index)
            self.conn.execute("ALTER TABLE runs ADD COLUMN total INTEGER DEFAULT 0")
        qcols = [r[1] for r in self.conn.execute("PRAGMA table_info(pictures)")]
        if "annotated" not in qcols:   # drawn-on screenshots (bug reports), annotations.py; hidden in the folder view by default
            for c, t in (("annotated", "INTEGER"), ("annotated_score", "TEXT"), ("annotated_by", "TEXT")):
                self.conn.execute(f"ALTER TABLE pictures ADD COLUMN {c} {t}")
        if "public" not in qcols:   # the owner: "Can use in public"; only these go on the website or into a portfolio PDF
            self.conn.execute("ALTER TABLE pictures ADD COLUMN public INTEGER DEFAULT 0")
            self.conn.execute("ALTER TABLE pictures ADD COLUMN public_at TEXT")
        if "no_public" not in qcols:   # older reverse flag, no longer used (every picture is private unless public = 1)   # the owner: "Cannot share public"; never used on the website or in a portfolio PDF
            self.conn.execute("ALTER TABLE pictures ADD COLUMN no_public INTEGER DEFAULT 0")
            self.conn.execute("ALTER TABLE pictures ADD COLUMN no_public_at TEXT")
        if "category" not in qcols:   # website, webapp, mobile, admin, store or diagram (category.py)
            self.conn.execute("ALTER TABLE pictures ADD COLUMN category TEXT")
            self.conn.execute("ALTER TABLE pictures ADD COLUMN category_by TEXT")
        pcols = [r[1] for r in self.conn.execute("PRAGMA table_info(projects)")]
        if "tech_override" not in pcols:   # the owner's own tech stack for a project (JSON list); wins over the written notes
            self.conn.execute("ALTER TABLE projects ADD COLUMN tech_override TEXT")
        if "portfolio" not in pcols:   # the owner's choice: 1 in the portfolio, 0 left out, NULL follows roles.py
            self.conn.execute("ALTER TABLE projects ADD COLUMN portfolio INTEGER")
        if "details" not in pcols:   # the written case-study notes (vault details), JSON
            for c in ("details", "details_at", "details_model"):
                self.conn.execute(f"ALTER TABLE projects ADD COLUMN {c} TEXT")
        if "created_from" not in pcols:   # the file whose date became the project's created_at (vault dates)
            self.conn.execute("ALTER TABLE projects ADD COLUMN created_from TEXT")
        if "merged_into" not in pcols:   # a project folded into a portfolio project (vault consolidate)
            self.conn.execute("ALTER TABLE projects ADD COLUMN merged_into INTEGER")
        if "min_priority" not in cols:   # an index run's lowest priority: the whole job it belongs to, across stops and restarts
            self.conn.execute("ALTER TABLE runs ADD COLUMN min_priority INTEGER")
        self.conn.commit()

    def x(self, sql: str, args=()):
        with _lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur

    def q(self, sql: str, args=()):
        with _lock:
            return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, args=()):
        with _lock:
            return self.conn.execute(sql, args).fetchone()


def import_seed(db: DB, seed: str = config.SEED_DB) -> dict:
    """Copy projects, units and read documents from an older catalogue, once. The seed is opened read only."""
    if db.one("SELECT 1 FROM projects LIMIT 1"):
        return {"skipped": "projects already here"}
    if not seed or not os.path.isfile(seed):
        raise SystemExit("No catalogue to import: set paths.seed_db in vault.toml (a SQLite file with a projects table).")
    src = sqlite3.connect(f"file:{seed}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    cols = [r[1] for r in db.conn.execute("PRAGMA table_info(projects)")]
    have = {r[1] for r in src.execute("PRAGMA table_info(projects)")}
    use = [c for c in cols if c in have]
    n = 0
    with _lock:
        for r in src.execute(f"SELECT {','.join(use)} FROM projects"):
            db.conn.execute(f"INSERT INTO projects({','.join(use)}) VALUES({','.join('?' * len(use))})", tuple(r))
            n += 1
        for r in src.execute("SELECT id, root, path, name FROM units"):
            db.conn.execute("INSERT OR IGNORE INTO units(id,root,path,name) VALUES(?,?,?,?)", tuple(r))
        for r in src.execute("SELECT unit_id, path, status FROM sources"):
            db.conn.execute("INSERT OR IGNORE INTO sources(unit_id,path,status) VALUES(?,?,?)", tuple(r))
        db.conn.commit()
    src.close()
    return {"projects": n, "units": db.one("SELECT COUNT(*) c FROM units")["c"], "sources": db.one("SELECT COUNT(*) c FROM sources")["c"]}


def project_dict(r) -> dict:
    d = dict(r)
    for k in ("tech", "domains", "evidence", "units"):
        try:
            d[k] = json.loads(d.get(k) or "[]")
        except ValueError:
            d[k] = []
    return d
