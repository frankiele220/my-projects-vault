"""What part of a product a kept picture shows: website, web app, mobile app, admin dashboard, store assets or a diagram.
Rules on the path, the picture's shape and its kind come first; JEV answers only when the rules cannot tell."""
from __future__ import annotations

import os
import re

CATS = {
    "website": "A public marketing or content website page: home page, landing page, product or company information, blog, "
               "with a navigation menu and a footer",
    "webapp": "A logged-in web application screen used by customers or end users in a browser: forms, lists, bookings, "
              "account and profile pages, member areas",
    "mobile": "A mobile app screen on a phone or tablet: narrow layout, tab bar, app header, touch-sized buttons",
    "admin": "An admin panel, CMS, back office or internal dashboard used by staff: tables of records, filters, settings, "
             "reports and charts",
    "store": "An app store or marketing image: a phone or tablet frame with a headline caption, a promotional screenshot, "
             "a banner or an icon set",
    "diagram": "A diagram: flowchart, architecture, data flow, database model, sitemap, wireframe sketch or org chart",
}
ORDER = ["mobile", "webapp", "website", "admin", "store", "diagram"]
LABEL = {"website": "Website", "webapp": "Web App", "mobile": "Mobile App", "admin": "Admin Dashboard",
         "store": "Store Assets", "diagram": "Diagrams"}

STORE = re.compile(r"store[ _-]?assets?|app[ _-]?store|play[ _-]?store|google[ _-]?play|itunes|store[ _-]?(screens?|listing)"
                   r"|screenshots?[ _-]?(for[ _-]?)?(ios|android)|promo(tional)?[ _-]?(screens?|images?)")
ADMIN = re.compile(r"\b(admin|cms|back[ _-]?office|back[ _-]?end[ _-]?(ui|screens?)|admin[ _-]?panel|console)\b")
MOBILE = re.compile(r"\b(ios|android|iphone|ipad|mobile|apk|ipa|react[ _-]?native|flutter)\b")
WEBSITE = re.compile(r"\b(website|web[ _-]?site|landing[ _-]?page|homepage|home[ _-]?page|wordpress|microsite|wp)\b")
WEBAPP = re.compile(r"\b(web[ _-]?app|webapp|portal|member[ _-]?area|dashboard)\b")


def by_rule(source: str, member: str, w: int, h: int, kind: str) -> str | None:
    """A category the path, shape or kind settles, or None."""
    if kind == "diagram":
        return "diagram"
    path = f"{os.path.dirname(source)}/{os.path.basename(source)}/{member or ''}".lower()
    if STORE.search(path):
        return "store"
    if ADMIN.search(path):
        return "admin"
    if w and h and h / w >= 1.6 and w <= 1600:   # tall and narrow: a phone screen
        return "mobile"
    if MOBILE.search(path):
        return "mobile"
    if WEBSITE.search(path):
        return "website"
    if WEBAPP.search(path):
        return "webapp"
    return None


def backfill(db, log=print) -> dict:
    """vault categorize: give a category to kept pictures that have none yet (rules, then JEV on the saved copy's text).
    Rests like a run (VAULT_REST) so the Mac stays cool."""
    import time
    from . import config, judge, vision
    rows = db.q("SELECT id, source, member, width, height, kind, file FROM pictures WHERE status IN ('kept','review','approved') "
                "AND file!='' AND category IS NULL")
    n_rule = n_jev = failed = 0
    for i, r in enumerate(rows, 1):
        cat = by_rule(r["source"], r["member"], r["width"] or 0, r["height"] or 0, r["kind"])
        if cat:
            db.x("UPDATE pictures SET category=?, category_by='rule' WHERE id=?", (cat, r["id"]))
            n_rule += 1
            continue
        t = time.monotonic()
        try:
            with open(r["file"], "rb") as f:
                img = vision.from_bytes(f.read())
            lines, _faces = vision.read(img)
            folder = os.path.relpath(os.path.dirname(r["source"]), config.ARCHIVE)
            state = judge.state_text(os.path.basename(r["source"]), folder, r["width"], r["height"], lines)
            cat, p = ask(state)
        except Exception as e:
            failed += 1
            log(f"{r['id']}: {e}")
            continue
        db.x("UPDATE pictures SET category=?, category_by=? WHERE id=?", (cat, f"jev {round(p * 100)}%", r["id"]))
        n_jev += 1
        if config.REST > 0:
            time.sleep(min(5.0, (time.monotonic() - t) * config.REST))
        if i % 100 == 0:
            log(f"{i}/{len(rows)}")
    return {"by_rule": n_rule, "by_jev": n_jev, "failed": failed, "total": len(rows)}


def ask(state: str) -> tuple:
    """(category, probability) from JEV, given the same picture description as the kind question."""
    from jev_style import choice
    from .judge import jev
    a = jev().decide(state, {"cat": choice("Which part of a software product does this picture show?", CATS)})["answers"]["cat"]
    return a["choice"], a["probabilities"][a["choice"]]
