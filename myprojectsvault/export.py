"""vault export: the portfolio as data for the website and the portfolio PDF. Every sharing rule lives here, so a site or PDF
builder only reads this output and never the database:

- every client project says it was delivered at the owner's agency (config.COMPANY, from vault.toml); the owner's own
  projects (client_type 'personal') say "Personal project" with their own role;
- "results" are internal notes and are never exported;
- a picture is exported only when the owner marked it "Can use in public" (public = 1), it is kept or approved (never
  'review', which may hold patient data) and its file exists; every other picture is private;
- only projects in the owner's portfolio (roles.py: website, back end, admin or server side, or the owner's own choice);
- client names are shown (an agency may name its clients once the work is finished);
  a project set to can_name_client = 0 hides its client's name.
"""
from __future__ import annotations

import json
import os

from . import config
from .db import DB

COMPANY = config.COMPANY
INTERNAL = ("results", "documents", "confidence")   # written notes kept inside the vault only


def project(db: DB, row: dict) -> dict:
    d = {}
    try:
        d = json.loads(row["details"] or "{}")
    except ValueError:
        pass
    for k in INTERNAL:
        d.pop(k, None)
    if row.get("tech_override"):   # the owner's own tech stack replaces the written one
        try:
            d["tech"] = json.loads(row["tech_override"])
        except ValueError:
            pass
    client = dict(d.pop("client", None) or {})
    if not client.get("name") and row.get("client_name"):   # the written notes left it out: the catalogue knows it
        client["name"] = row["client_name"]
    if not row["can_name_client"]:
        client.pop("name", None)
    pics = [dict(r) for r in db.q("SELECT id, file, category, kind, width, height FROM pictures WHERE project_id=? AND status IN "
                                  "('kept','approved') AND COALESCE(public,0)=1 AND file!='' ORDER BY category, id", (row["id"],))]
    pics = [p for p in pics if os.path.isfile(p["file"])]
    from .roles import effective
    d.pop("loc_role", None)   # older notes
    d.pop("owner_role", None)   # the documents' wording stays inside the vault; the export states the role by the owner's rule
    personal = row.get("client_type") == "personal"   # the owner's own tool or product, not client work
    role = d.pop("role", None)
    return {"id": row["id"], "name": row["name"], "delivered_at": "Personal project" if personal else COMPANY, "client": client,
            "my_role": (role or ["Product Owner", "Engineer"]) if personal
            else effective(row["details"], row["tech"], row["portfolio"], row["name"])["roles"],
            "created": (row["created_at"] or "")[:10] if (row["created_at"] or "")[:4].isdigit() else None,
            **d, "pictures": pics}


def run(db: DB, out: str = "", with_pictures_only: bool = False) -> dict:
    rows = [dict(r) for r in db.q("SELECT * FROM projects WHERE merged_into IS NULL AND status!='hidden' ORDER BY name COLLATE NOCASE")]
    from .roles import effective
    # the owner's portfolio holds only projects they worked on directly (website, back end, admin, server side) or chose themselves
    rows = [r for r in rows if effective(r["details"], r["tech"], r["portfolio"], r["name"])["in_portfolio"]]
    items = [project(db, r) for r in rows]
    if with_pictures_only:
        items = [i for i in items if i["pictures"]]
    data = {"company": COMPANY, "owner": config.OWNER,
            "note": f"Client projects were delivered at {COMPANY}." if COMPANY else "", "projects": items}
    if out:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    return {"projects": len(items), "pictures": sum(len(i["pictures"]) for i in items), "out": out or None}
