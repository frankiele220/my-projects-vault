"""vault publish: build the portfolio website page and the portfolio PDF from `vault export` only (export.py applies every
sharing rule: delivered at the owner's agency, no results, only pictures marked "Can use in public", only projects in the
owner's portfolio). Projects are listed most recent first; a project without public pictures is shown as text only.

  vault publish --site ~/my-site/portfolio --pdf ~/Desktop/Portfolio.pdf
"""
from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
import tempfile

from . import config, export
from .db import DB

CHROME = config.CHROME
OWNER = config.OWNER or "Portfolio"
WEB_PX = 1600
FEATURED = 25          # projects shown in full; the others go in a compact list
MAX_PICS = 5           # pictures per project
SHORT = {"problems": 2, "solution": 3, "users": 4, "key_features": 6, "tech": 8}
LABEL = {"website": "Website", "webapp": "Web App", "mobile": "Mobile App", "admin": "Admin Dashboard",
         "store": "Store Assets", "diagram": "Diagram"}


def e(s) -> str:
    return html.escape(str(s or ""), quote=True)


def years_of(p: dict) -> tuple:
    """(first year, last year) from the written years or the created date; (0, 0) when unknown."""
    ys = [int(y) for y in re.findall(r"\b(20[0-3]\d|19[89]\d)\b", f"{p.get('years') or ''}")]
    if not ys and p.get("created"):
        ys = [int(p["created"][:4])]
    return (min(ys), max(ys)) if ys else (0, 0)


def when(p: dict) -> str:
    a, b = years_of(p)
    return "" if not a else (str(a) if a == b else f"{a} – {b}".replace(" – ", " to "))


def ordered(projects: list) -> list:
    """Most recent first: by last year, then first year, then name."""
    return sorted(projects, key=lambda p: (-years_of(p)[1], -years_of(p)[0], p["name"].lower()))


def jpeg(src: str, dst: str, px: int = WEB_PX) -> tuple:
    """A JPEG copy at most px wide or tall (macOS ImageIO); (width, height)."""
    import Quartz
    from Foundation import NSURL
    s = Quartz.CGImageSourceCreateWithURL(NSURL.fileURLWithPath_(src), None)
    img = Quartz.CGImageSourceCreateThumbnailAtIndex(s, 0, {Quartz.kCGImageSourceCreateThumbnailFromImageAlways: True,
                                                         Quartz.kCGImageSourceThumbnailMaxPixelSize: px,
                                                         Quartz.kCGImageSourceCreateThumbnailWithTransform: True})
    d = Quartz.CGImageDestinationCreateWithURL(NSURL.fileURLWithPath_(dst), "public.jpeg", 1, None)
    Quartz.CGImageDestinationAddImage(d, img, {Quartz.kCGImageDestinationLossyCompressionQuality: 0.84})
    Quartz.CGImageDestinationFinalize(d)
    return Quartz.CGImageGetWidth(img), Quartz.CGImageGetHeight(img)


def _list(items) -> str:
    items = [i for i in (items or []) if i]
    return "<ul>" + "".join(f"<li>{e(i)}</li>" for i in items) + "</ul>" if items else ""


def _chips(items) -> str:
    items = [i for i in (items or []) if i]
    return '<div class="chips">' + "".join(f'<span class="chip">{e(i)}</span>' for i in items) + "</div>" if items else ""


def sentences(text: str, n: int = 2) -> str:
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    return " ".join(parts[:n])


def shorten(p: dict) -> dict:
    """The written notes, cut to what a reader takes in at a glance."""
    q = dict(p)
    q["overview"] = sentences(p.get("overview"), 2)
    for k, n in SHORT.items():
        q[k] = (p.get(k) or [])[:n]
    q["pictures"] = (p.get("pictures") or [])[:MAX_PICS]
    return q


def importance(p: dict, kept: dict) -> float:
    """Which projects deserve a full card: public pictures first, then the owner's focus field (config FOCUS), size and recency."""
    c = p.get("client") or {}
    focus = bool(config.FOCUS and re.search(config.FOCUS, f"{c.get('industry')} {p['name']}", re.I))
    return (100 * bool(p.get("pictures")) + 40 * focus + min(kept.get(p["id"], 0), 100) / 2
            + 3 * max(0, years_of(p)[1] - 2013))


def compact(p: dict) -> str:
    c = p.get("client") or {}
    who = " · ".join(x for x in [c.get("name"), c.get("country")] if x)
    return (f'<li><span class="cn">{e(p["name"])}</span><span class="cw">{e(when(p))}</span>'
            f'{f"<span class=cc>{e(who)}</span>" if who else ""}<span class="co">{e(sentences(p.get("overview"), 1))}</span></li>')


def card(p: dict, img_src) -> str:
    p = shorten(p)
    c = p.get("client") or {}
    meta = " · ".join(x for x in [c.get("name"), c.get("country"), c.get("industry")] if x)
    users = "".join(f"<li><b>{e(u.get('type'))}</b>{(': ' + e(u.get('needs'))) if u.get('needs') else ''}</li>"
                    for u in (p.get("users") or []) if isinstance(u, dict) and u.get("type"))
    where = (" · delivered at " + e(p.get("delivered_at"))) if p.get("delivered_at") == export.COMPANY else " · personal project"
    demo = (f'<a href="{e(p["demo_url"])}" target="_blank" rel="noopener">Watch the demo</a>' if p.get("demo_url") else "")
    rows = [("My role", e(", ".join(p.get("my_role") or [])) + where), ("Demo", demo),
            ("Problems", _list(p.get("problems"))), ("Solution", _list(p.get("solution"))),
            ("Users", _chips([u.get("type") for u in (p.get("users") or []) if isinstance(u, dict)])), ("Platforms", _chips(p.get("platforms"))),
            ("Key features", _chips(p.get("key_features"))), ("Tech", _chips(p.get("tech")))]
    dl = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in rows if v)
    pics = ""
    if p.get("pictures"):
        pics = '<div class="pics">' + "".join(
            f'<a class="pic" href="{img_src(x)}" data-full="{img_src(x)}"><img src="{img_src(x)}" loading="lazy" '
            f'alt="{e(LABEL.get(x.get("category"), "Screen"))} of {e(p["name"])}"><span>{e(LABEL.get(x.get("category"), ""))}</span></a>'
            for x in p["pictures"]) + "</div>"
    return (f'<article class="proj" id="p{p["id"]}"><header><h3>{e(p["name"])}</h3><span class="when">{e(when(p))}</span></header>'
            f'{f"<p class=meta>{e(meta)}</p>" if meta else ""}<p class="ov">{e(p.get("overview"))}</p><dl>{dl}</dl>{pics}</article>')


CSS = """:root{--bg:#fdf4e6;--surface:#fff;--ink:#16181d;--muted:#5d5a52;--line:#e9dcc6;--acc:#b5610c;--chip:#f6ead6}
@media screen and (prefers-color-scheme:dark){:root{--bg:#1b1915;--surface:#23201b;--ink:#f3ede2;--muted:#b6ad9e;--line:#3a342b;--acc:#f3a04a;--chip:#2c2822}}
*{box-sizing:border-box}html{scroll-behavior:smooth}html,body{overflow-x:hidden;max-width:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 "Helvetica Neue",Helvetica,Arial,system-ui,sans-serif}
.wrap{max-width:1040px;margin:0 auto;padding:32px 16px 64px}
.top{display:flex;align-items:center;gap:18px}.me{flex:none;width:96px;height:96px;border-radius:50%;object-fit:cover;border:4px solid var(--surface);box-shadow:0 4px 14px rgba(0,0,0,.12)}
.top h1{font-size:clamp(28px,5vw,40px);line-height:1.1;margin:0 0 6px}.top p{color:var(--muted);margin:0}
.lead{margin:18px 0 0;max-width:760px}.dl{display:inline-block;margin-top:12px;font-weight:700;color:var(--acc)}
nav{position:sticky;top:0;z-index:2;background:var(--bg);border-bottom:1px solid var(--line);margin:24px 0 8px;padding:8px 0;display:flex;gap:4px;overflow-x:auto}
nav a{flex:none;color:var(--muted);text-decoration:none;font-weight:600;font-size:14px;padding:4px 10px;border-radius:999px;min-height:28px}
nav a:hover,nav a:focus-visible{background:var(--chip);color:var(--ink)}
h2{font-size:22px;margin:32px 0 12px;scroll-margin-top:56px}h2 small{font-size:14px;color:var(--muted);font-weight:400}
.proj{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:20px;margin:0 0 16px}
.proj header{display:flex;flex-wrap:wrap;gap:4px 12px;align-items:baseline;justify-content:space-between}
.proj h3{margin:0;font-size:19px;line-height:1.3}.when{font-size:13px;color:var(--muted);font-weight:600}
.meta{margin:4px 0 0;color:var(--acc);font-size:14px;font-weight:600}.ov{margin:10px 0 0}
dl{display:grid;grid-template-columns:130px 1fr;gap:6px 16px;margin:14px 0 0;font-size:15px}
dt{color:var(--muted);font-size:13px;font-weight:600;padding-top:2px}dd{margin:0;min-width:0;overflow-wrap:anywhere}dd ul{margin:0;padding-left:18px}dd li{margin-bottom:2px}
.chips{display:flex;flex-wrap:wrap;gap:6px;min-width:0}.chip{background:var(--chip);border-radius:999px;padding:2px 10px;font-size:13px;max-width:100%;overflow-wrap:anywhere}
.proj,.proj>*{min-width:0}.proj h3,.meta,.ov{overflow-wrap:anywhere}
.more{list-style:none;margin:0;padding:0;display:grid;gap:8px}
.more li{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:10px 14px;display:grid;grid-template-columns:1fr auto;gap:2px 12px;min-width:0}
.more .cn{font-weight:700;overflow-wrap:anywhere}.more .cw{font-size:13px;color:var(--muted);font-weight:600;text-align:right}
.more .cc{grid-column:1/-1;font-size:13px;color:var(--acc);font-weight:600}.more .co{grid-column:1/-1;font-size:14px;color:var(--muted)}
.pics{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,180px),1fr));gap:10px;margin-top:16px}
.pic{position:relative;display:block;border:1px solid var(--line);border-radius:10px;overflow:hidden;background:var(--chip)}
.pic img{display:block;width:100%;height:170px;object-fit:contain}
.pic span{position:absolute;left:6px;bottom:6px;font-size:11px;background:rgba(0,0,0,.6);color:#fff;padding:1px 7px;border-radius:999px}
.pic span:empty{display:none}
dialog{border:0;padding:0;background:transparent;max-width:96vw;max-height:94vh}dialog::backdrop{background:rgba(0,0,0,.85)}
dialog img{display:block;max-width:96vw;max-height:94vh;border-radius:8px}
@media(max-width:640px){dl{grid-template-columns:minmax(0,1fr)}dt{padding-top:8px}.proj{padding:16px}.top{align-items:flex-start}.top .me{width:72px;height:72px}
 .pics{grid-template-columns:repeat(2,minmax(0,1fr))}.pic img{height:140px}}
"""

PRINT = """@page{size:A4;margin:14mm 13mm 16mm}
body{font-size:10.5pt;background:#fff}.wrap{max-width:none;padding:0}
.cover{height:262mm;display:flex;flex-direction:column;justify-content:center;page-break-after:always}
.cover .me{width:120px;height:120px}.cover h1{font-size:34pt}
.proj{break-inside:auto;border:0;border-top:2px solid var(--acc);border-radius:0;padding:10px 0 4px;margin:0 0 14px}
.proj header,.proj .meta,.proj .ov{break-after:avoid}.proj.big{break-before:page}
dl{grid-template-columns:105px 1fr;font-size:10pt;gap:4px 12px}dt{font-size:9pt}
.pics{grid-template-columns:repeat(3,1fr);gap:8px}.pic{break-inside:avoid}.pic img{height:150px}.more li{break-inside:avoid}
h2{break-after:avoid;font-size:16pt;margin:18px 0 8px}
"""


def page(data: dict, img_src, me_src: str, pdf_link: str = "", for_print: bool = False, kept: dict | None = None) -> str:
    kept = kept or {}
    ranked = sorted(data["projects"], key=lambda p: -importance(p, kept))
    projects = ordered(ranked[:FEATURED])
    others = ordered(ranked[FEATURED:])
    n_pics = sum(min(MAX_PICS, len(p.get("pictures") or [])) for p in projects)
    groups, last = [], None
    for p in projects:
        y = years_of(p)[1] or "Undated"
        if y != last:
            groups.append((y, []))
            last = y
        groups[-1][1].append(p)
    lead = ((f'Client projects below were delivered at <b>{e(data["company"])}</b>, where I worked as team lead and lead '
             f'engineer; my own projects are marked as personal. Most recent first.') if data.get("company") else "Most recent first.")
    head = (f'<div class="top{" cover" if for_print else ""}"><img class="me" src="{me_src}" width="96" height="96" alt="{e(OWNER)}">'
            f'<div><h1>{e(OWNER)}</h1><p>{len(projects)} selected project{"s" if len(projects) != 1 else ""} · {len(others)} more listed · {n_pics} screens'
            + ("" if for_print else " · private page, please do not share") + f'</p><p class="lead">{lead}</p>'
            + (f'<a class="dl" href="{pdf_link}" download>Download the PDF portfolio</a>' if pdf_link and not for_print else "")
            + "</div></div>")
    if for_print:   # the cover stands on its own page: the photo above the name
        head = (f'<section class="cover"><img class="me" src="{me_src}" alt="{e(OWNER)}"><h1>{e(OWNER)}</h1>'
                f'<p>Portfolio · {len(projects)} selected projects · {len(others)} more listed · {n_pics} screens</p><p class="lead">{lead}</p></section>')
    nav = "" if for_print else ('<nav aria-label="Years">' + "".join(f'<a href="#y{y}">{y}</a>' for y, _ in groups)
                                 + ('<a href="#more">More projects</a>' if others else "") + "</nav>")
    body = "".join(f'<h2 id="y{y}">{y} <small>{len(ps)} project{"s" if len(ps) != 1 else ""}</small></h2>'
                   + "".join(card(p, img_src) for p in ps) for y, ps in groups)
    if others:
        body += (f'<h2 id="more">More projects <small>{len(others)}, most recent first</small></h2>'
                 f'<ul class="more">{"".join(compact(p) for p in others)}</ul>')
    script = "" if for_print else """<dialog id="big" aria-label="Screen"><img alt=""></dialog><script>
const d=document.getElementById("big"),im=d.querySelector("img");
document.addEventListener("click",e=>{const a=e.target.closest("a.pic");if(a){e.preventDefault();im.src=a.dataset.full;im.alt=a.querySelector("img").alt;d.showModal();}
 else if(e.target===d||e.target===im)d.close();});</script>"""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="robots" content="noindex,nofollow,noarchive"><title>Portfolio · {e(OWNER)}</title>'
            f'<style>{CSS}{PRINT if for_print else ""}</style></head><body><div class="wrap">{head}{nav}{body}</div>{script}</body></html>')


def run(db: DB, site: str = "", pdf: str = "", only: str = "", log=print) -> dict:
    tmp = tempfile.mkdtemp(prefix="vault-publish-")
    data_path = os.path.join(tmp, "portfolio.json")
    export.run(db, data_path)
    import json
    data = json.load(open(data_path, encoding="utf-8"))
    if only:   # a preview of some projects
        data["projects"] = [p for p in data["projects"] if only.lower() in p["name"].lower()]
    kept = {r["project_id"]: r["c"] for r in db.q("SELECT project_id, COUNT(*) c FROM pictures WHERE status IN ('kept','approved') "
                                                  "GROUP BY project_id")}
    featured = {p["id"] for p in sorted(data["projects"], key=lambda p: -importance(p, kept))[:FEATURED]}
    # pictures: one web-sized JPEG each, named by picture id
    img_dir = os.path.join(site, "img") if site else os.path.join(tmp, "img")
    if site and os.path.isdir(img_dir):
        shutil.rmtree(img_dir)   # pictures no longer public must not stay on the site
    os.makedirs(img_dir, exist_ok=True)
    for p in data["projects"]:
        for x in (p.get("pictures") or [])[:MAX_PICS] if p["id"] in featured else []:
            jpeg(x["file"], os.path.join(img_dir, f'{x["id"]}.jpg'))
    out = {"projects": len(data["projects"]), "featured": len(featured),
           "pictures": sum(min(MAX_PICS, len(p.get("pictures") or [])) for p in data["projects"] if p["id"] in featured)}
    if site:
        pdf_name = config.PDF_NAME
        with open(os.path.join(site, "index.html"), "w", encoding="utf-8") as f:
            f.write(page(data, lambda x: f'/portfolio/img/{x["id"]}.jpg', config.OWNER_PHOTO, f"/portfolio/{pdf_name}" if pdf else "", kept=kept))
        out["site"] = site
    if pdf:
        me = os.path.join(os.path.dirname(os.path.abspath(site)), config.OWNER_PHOTO.lstrip("/")) if site and config.OWNER_PHOTO else ""
        html_path = os.path.join(tmp, "print.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(page(data, lambda x: "file://" + os.path.join(img_dir, f'{x["id"]}.jpg'),
                         "file://" + me if me and os.path.isfile(me) else "", for_print=True, kept=kept))
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={pdf}",
                        "--allow-file-access-from-files", "--virtual-time-budget=20000", "file://" + html_path],
                       check=True, capture_output=True, timeout=300)
        out["pdf"] = pdf
        if site:
            shutil.copy(pdf, os.path.join(site, config.PDF_NAME))
    shutil.rmtree(tmp, ignore_errors=True)
    return out
