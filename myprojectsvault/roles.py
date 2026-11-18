"""The owner's role in a project, by the owner's rule: when the project has a website, a back end, an admin dashboard or
server-side work, the owner took part directly as Team Lead and Lead Engineer, and the project goes into their portfolio. A project
with only a mobile app (or no sign of a website, back end or admin) had little of the owner's direct work: it is marked not
directly involved and left out of the portfolio. The owner can override per project (projects.portfolio: 1 include, 0 exclude).
No model is asked; the rule reads the written details (platforms, tech, features) and the catalogue's tech list."""
from __future__ import annotations

import json
import re

HANDS_ON = ["Team Lead", "Lead Engineer"]
NOT_MINE = ["Not directly involved"]
PLATFORMS = re.compile(r"website|web app|admin|dashboard|api|desktop", re.I)
SERVER = re.compile(r"\b(back[ -]?end|server|api|database|mysql|postgres|mongo|sql|aws|azure|gcp|firebase|php|laravel|node|"
                    r"express|django|python|java|\.net|c#|ruby|rails|wordpress|drupal|joomla|magento|woocommerce|odoo|cms|"
                    r"admin|dashboard|cron|webhook|integration|hosting|devops|docker|website|web ?site|site|dns|domain|portal|seo)\b", re.I)


def _list(v) -> list:
    if isinstance(v, list):
        return [str(x) for x in v]
    if isinstance(v, str):
        try:
            x = json.loads(v)
            return [str(i) for i in x] if isinstance(x, list) else [v]
        except ValueError:
            return [v]
    return []


def role_of(details, tech=None, name: str = "") -> dict:
    """{"roles": [...], "basis": why, "in_portfolio": bool}; details is the project's written notes (dict or JSON text)."""
    d = details if isinstance(details, dict) else (json.loads(details) if details else {})
    platforms = _list(d.get("platforms"))
    hits = [p for p in platforms if PLATFORMS.search(p)]
    if hits:
        return {"roles": HANDS_ON, "basis": "has " + ", ".join(hits), "in_portfolio": True}
    if platforms and all(re.search(r"mobile|ios|android|tablet", p, re.I) for p in platforms):
        # only mobile apps: every app calls some API, so tech words do not make it the owner's hands-on work
        return {"roles": NOT_MINE, "basis": "mobile app only", "in_portfolio": False}
    words = " ".join(_list(d.get("tech")) + _list(tech) + _list(d.get("key_features")) + [name or ""])
    m = SERVER.search(words)
    if m:
        return {"roles": HANDS_ON, "basis": f"server-side work ({m.group(0)})", "in_portfolio": True}
    mobile = any(re.search(r"mobile|ios|android", p, re.I) for p in platforms)
    return {"roles": NOT_MINE, "basis": "mobile app only" if mobile else "no website, back end or admin found",
            "in_portfolio": False}


def effective(details, tech=None, override=None, name: str = "") -> dict:
    """role_of plus the owner's own choice: override 1 includes, 0 excludes, None follows the rule."""
    try:
        r = role_of(details, tech, name)
    except ValueError:
        r = {"roles": [], "basis": "no details yet", "in_portfolio": False}
    r["auto"] = r["in_portfolio"]
    r["override"] = override
    if override is not None:
        r["in_portfolio"] = bool(override)
        if override and r["roles"] == NOT_MINE:
            r["roles"] = HANDS_ON
        r["basis"] += " · set by the owner: " + ("include" if override else "exclude")
    return r
