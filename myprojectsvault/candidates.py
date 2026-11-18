"""Step 2: which projects a document could belong to. The list goes to JEV as the options of one question."""
from __future__ import annotations

import json
import os
import re

from . import config
from .db import DB

GENERIC = set("""website web site platform system app application project portal dashboard mobile management design module
modules phase release reports report the and for new client feature features tool tools data service services online backup
copy final draft notes document documents files folder""".split())
MAX_OPTIONS = 25
FOLDER_GENERIC = GENERIC | set(config.GENERIC_FOLDERS) | set("""web app new old the and for with v1 v2 v3 2014 2015 2016 2017 2018 2019 2020 2021 2022 2023 2024 2025
2026 job sheet sheets archive archives others other misc doc docs screenshots screenshot screen shot images image img assets
mockup mockups layout layouts ver final phase ios ipad iphone android mobile tablet phone desktop design designs ui ux
store links link source sources export exports png jpg psd sketch figma""".split())


def segment_words(seg: str) -> set:
    """The name words of one folder name, without a leading date or number ("160103 - Acme v2" -> {"acme"})."""
    seg = re.sub(r"^\s*\d{4,8}\s*[-_.]\s*", "", seg.lower())
    return {w for w in re.findall(r"[a-z0-9]+", seg) if not re.fullmatch(r"v?\d+(\.\d+)*", w)} - FOLDER_GENERIC


def words(text: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]{4,}", (text or "").lower()) if w not in GENERIC}


class Projects:
    def __init__(self, db: DB):
        rows = db.q("SELECT id, name, client_name, client_type, industry, summary, units, evidence, merged_into FROM projects "
                    "WHERE status!='hidden'")
        # projects folded into a portfolio project (merged_into) still help to recognise documents by their names, folders and
        # sources, but every answer is the portfolio project they were folded into; only portfolio projects are offered to JEV
        self.canon = {r["id"]: r["merged_into"] or r["id"] for r in rows}
        c = self.canon.get
        self.p = {r["id"]: dict(r) for r in rows if not r["merged_into"]}
        self.by_unit: dict = {}
        self.by_doc: dict = {}
        for r in rows:
            for u in json.loads(r["units"] or "[]"):
                self.by_unit.setdefault(u, set()).add(c(r["id"]))
            for e in json.loads(r["evidence"] or "[]"):
                self.by_doc.setdefault(os.path.basename(str(e)).lower(), set()).add(c(r["id"]))
        self.words = {r["id"]: words(f"{r['client_name']} {r['name']}") for r in rows}
        # for folder names: words of 3+ letters (acronyms such as ACM, NWD, LMN count), without words every project shares
        self.fwords = {r["id"]: set(re.findall(r"[a-z0-9]{3,}", f"{r['client_name'] or ''} {r['name'] or ''}".lower())) - FOLDER_GENERIC
                       for r in rows}
        units = db.q("SELECT id, path FROM units")
        self.folders = sorted(((u["path"], u["id"]) for u in units if "#loose" not in u["path"]), key=lambda t: -len(t[0]))
        self.loose = {r["path"]: r["unit_id"] for r in db.q("SELECT s.path, s.unit_id FROM sources s JOIN units u ON u.id=s.unit_id "
                                                             "WHERE u.path LIKE '%#loose%'")}
        self.sensitive_units = {r["unit_id"] for r in db.q("SELECT DISTINCT unit_id FROM sources WHERE status='sensitive'")}

    def units_of(self, path: str) -> list:
        out = []
        if path in self.loose:
            out.append(self.loose[path])
        for folder, uid in self.folders:   # the deepest folder that holds the file
            if path.startswith(folder + "/"):
                out.append(uid)
                break
        return out

    def for_file(self, path: str, root: str) -> list:
        """Project ids, most likely first: projects that list this document, projects found in its folder, projects whose name
        or client is in the file's path."""
        doc = os.path.basename(path).lower()
        rel = os.path.relpath(path, root).lower()
        ordered = list(self.by_doc.get(doc, []))
        for u in self.units_of(path):
            ordered += self.by_unit.get(u, [])
        hits = sorted(((sum(1 for w in ws if w in rel), pid) for pid, ws in self.words.items() if ws), reverse=True)
        ordered += [self.canon[pid] for n, pid in hits if n]
        return list(dict.fromkeys(ordered))[:MAX_OPTIONS]

    def options(self, path: str, root: str) -> list:
        """Every project the rules point at, for JEV to choose from: the catalogue's sure match, projects named by the folder
        names, then projects found by the document, its folder and its path words."""
        first = [self.named_by_folder(path), self.direct(path)] + self.by_folder_names(path)
        return list(dict.fromkeys([p for p in first if p is not None] + self.for_file(path, root)))[:MAX_OPTIONS]

    def direct(self, path: str) -> int | None:
        """A project that is certain without asking: the only project listing this document as a source, or the only project
        found in the document's own folder (not a batch of loose files)."""
        owners = set(self.by_doc.get(os.path.basename(path).lower(), []))
        if len(owners) == 1:
            return next(iter(owners))
        folder = next((uid for f, uid in self.folders if path.startswith(f + "/")), None)   # the deepest real folder, never a loose batch
        pids = set(self.by_unit.get(folder, [])) if folder is not None else set()
        if len(pids) == 1:
            return next(iter(pids))
        best = self.by_folder_names(path)
        return best[0] if len(best) == 1 else None

    def by_folder_names(self, path: str) -> list:
        """Projects whose name or client shows in the folder names of the path ("Northwind - Helios", "Orbit - Lumen"),
        best first; only those tied for the best match. An image file in such a folder belongs to that project."""
        dirs = os.path.dirname(path).lower()
        tokens = set(re.findall(r"[a-z0-9]{3,}", dirs)) - FOLDER_GENERIC
        if not tokens:
            return []
        # a folder named exactly like a word of a project ("CRM", "ACM") is a strong sign even though the word is short
        whole = {seg.strip() for seg in dirs.split("/")} - FOLDER_GENERIC
        # a folder whose name is only this project's words ("160103 - Acme v2", "Northwind - Helios") names the project
        named = [segment_words(seg) for seg in dirs.split("/")]
        named = [s for s in named if s]
        scored = []
        for pid, ws in self.fwords.items():
            hit = ws & tokens
            strong = bool(ws & whole) or any(s <= ws for s in named)
            if hit and (sum(map(len, hit)) >= 5 or strong):
                scored.append((len(hit) + (1 if strong else 0), sum(map(len, hit)), pid))
        if not scored:
            return []
        scored.sort(reverse=True)
        top = scored[0][:2]
        return list(dict.fromkeys(self.canon[pid] for n, ln, pid in scored if (n, ln) == top))

    def named_by_folder(self, path: str) -> int | None:
        """The one project a folder of the path is named after ("160103 - Acme v2" -> Acme): every name word of that folder is
        a word of the project. The deepest such folder wins; None when no folder or more than one project fits."""
        for seg in reversed(os.path.dirname(path).split("/")):
            ws = segment_words(seg)
            if not ws:
                continue
            fits = {self.canon[pid] for pid, pw in self.fwords.items() if ws <= pw}
            if len(fits) == 1:
                return next(iter(fits))
            if fits:
                return None
        return None

    def risky_folder(self, path: str) -> bool:
        return any(u in self.sensitive_units for u in self.units_of(path))

    def label(self, pid: int) -> str:
        p = self.p[pid]   # name, client and industry only: a long summary drowns the path in JEV's answer
        return " | ".join(b for b in [p["name"], p["client_name"] or p["client_type"], p["industry"]] if b)
