"""vault consolidate: fold the catalogue's many entries (one per SOW, release, ticket...) into portfolio projects.

The groups live in data/portfolio-groups.json: {"groups": {"Portfolio project name": [old project ids]}, "exclude": {id: why}}.
Per group the old entry with the most pictures becomes the portfolio project and takes the group's name and the years of all its
entries; the others point to it (merged_into) and stay as aliases, so their names, folders and sources still recognise documents.
Pictures move to the portfolio project. Running it again with an edited file re-applies the groups (names already given stay
until the file gives another)."""
from __future__ import annotations

import json
import os
import re

from .db import DB, now

GROUPS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "portfolio-groups.json")


def _years(texts) -> str:
    ys = sorted({int(y) for t in texts for y in re.findall(r"\b(20[0-3]\d)\b", t or "")})
    if not ys:
        return ""
    return str(ys[0]) if ys[0] == ys[-1] else f"{ys[0]} to {ys[-1]}"


def run(db: DB, path: str = GROUPS) -> dict:
    spec = json.load(open(path, encoding="utf-8"))
    groups, exclude = spec["groups"], {int(k): v for k, v in spec.get("exclude", {}).items()}
    seen: dict = {}
    for g, ids in groups.items():
        for i in ids:
            if i in seen:
                raise ValueError(f"project {i} is in two groups: {seen[i]} and {g}")
            seen[i] = g
    # re-apply from the file: earlier links are cleared and set again; pictures are moved again to their group's project
    db.x("UPDATE projects SET merged_into=NULL")
    pics = {r["project_id"]: r["c"] for r in db.q("SELECT project_id, COUNT(*) c FROM pictures GROUP BY project_id")}
    moved = 0
    for g, ids in groups.items():
        rows = [dict(r) for r in db.q(f"SELECT id, name, years FROM projects WHERE id IN ({','.join('?' * len(ids))})", tuple(ids))]
        if not rows:
            continue
        head = max(rows, key=lambda r: (pics.get(r["id"], 0), -r["id"]))
        years = _years([r["years"] for r in rows] + [r["name"] for r in rows]) or head["years"]
        db.x("UPDATE projects SET name=?, years=?, updated_at=? WHERE id=?", (g, years, now(), head["id"]))
        others = [r["id"] for r in rows if r["id"] != head["id"]]
        if others:
            q = ",".join("?" * len(others))
            db.x(f"UPDATE projects SET merged_into=? WHERE id IN ({q})", (head["id"], *others))
            moved += db.x(f"UPDATE pictures SET project_id=? WHERE project_id IN ({q})", (head["id"], *others)).rowcount
    for i, why in exclude.items():
        db.x("UPDATE projects SET status='hidden', updated_at=? WHERE id=?", (now(), i))
    left = db.one("SELECT COUNT(*) c FROM projects WHERE merged_into IS NULL AND status!='hidden'")["c"]
    return {"groups": len(groups), "folded": sum(len(v) for v in groups.values()) - len(groups), "pictures_moved": moved,
            "hidden": len(exclude), "portfolio_projects": left}
