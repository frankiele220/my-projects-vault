"""vault details: write each portfolio project's details (problems it solved, solution, kinds of users, client country, years,
features, tech, the owner's role) from its own documents, through a writing model on OpenRouter.

What is sent: the project's catalogue text (its own entry and every entry folded into it) and excerpts of its documents'
text, with every line that looks private removed (names, e-mail, phone, IDs, dates of birth, addresses...). Documents marked
sensitive are skipped, healthcare documents are filtered with the stricter rules. Pictures are never sent.
The key comes from OPENROUTER_API_KEY or ~/.config/myprojectsvault/openrouter.key (one line, chmod 600)."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request

from . import config, find, privacy
from .db import DB, now

KEY_FILE = os.path.expanduser("~/.config/myprojectsvault/openrouter.key")
MODEL = os.environ.get("VAULT_LLM_MODEL", "google/gemini-2.5-flash")
URL = "https://openrouter.ai/api/v1/chat/completions"
DOC_CHARS = 2500        # per document
ALL_CHARS = 18000       # per project
MAX_DOCS = 10
GOOD = re.compile(r"sow|scope|proposal|brief|requirement|spec|overview|release|roadmap|feature|user ?flow|introduc|summary|"
                  r"presentation|deck|plan|estimat|quotation|contract|feedback", re.I)

PROMPT = """You write portfolio case-study notes for a software delivery lead (the owner). Use ONLY the material below about one
project: catalogue notes and excerpts of the project's own documents. Never invent facts; when something is not in the
material, use null or an empty list. Lines that looked private were removed before you saw the text.

Context: an agency (AGENCY) delivered this project for a client.
- "client" is the company that owns the product: not the agency, and not a partner, vendor or lab named in one entry.
  The catalogue entries below were folded together because they are phases or parts of the same product.
- "problems": the business or user problems the product solves, i.e. why the client needed it. Never list software bugs,
  QA findings, tickets, change requests or project-management issues. When the documents are mostly bug lists, release
  notes or task sheets, state the problems at the level of why the product exists, using the catalogue notes and what the
  product clearly does. At most 5 items.
  Wrong: "Customers could not complete sign-up due to a missing gender field." / "Promo codes were not working."
  Right: "Shoppers had no simple way to order goods online and get them delivered to their door."
         "Vendors needed one place to manage stock, orders and sales reports."
- "solution": what the product does for its users and how it solves those problems, not task lists or bug fixes.
- "country": the client's country as a full English name ("United States", "Singapore", "United Kingdom"); null when it
  is not stated. Never a region such as "Asia".
- "results": internal notes only (never published): launches, go-lives or outcomes stated in the material.

Return JSON only, with these keys:
{
 "overview": "2 to 3 sentences: what the product is, who it is for and what it lets them do",
 "problems": ["the business or user problems the product solves, one short sentence each, 2 to 5 items"],
 "solution": ["how the product solves them for its users, one short sentence each, 2 to 6 items"],
 "users": [{"type": "a kind of user, e.g. Patient, Provider, Admin, Sponsor, Driver", "needs": "what they do in the product, one sentence"}],
 "client": {"name": "client or brand, or null", "country": "client's country, or null", "industry": "industry, or null"},
 "years": "the period worked on, e.g. '2015 to 2017', from dates in the material, or null",
 "platforms": ["of: Website, Web App, Mobile App (iOS), Mobile App (Android), Admin Dashboard, API, Desktop App"],
 "key_features": ["up to 8 short feature names"],
 "tech": ["technologies named in the material"],
 "owner_role": "the portfolio owner's or the team's role if the material says so, else null",
 "results": ["outcomes or launches stated in the material, else empty"],
 "confidence": "high, medium or low: how well the material supports these notes"
}

MATERIAL
"""


def api_key() -> str:
    k = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not k and os.path.isfile(KEY_FILE):
        k = open(KEY_FILE, encoding="utf-8").read().strip()
    if not k:
        raise SystemExit(f"No OpenRouter key: set OPENROUTER_API_KEY or put it in {KEY_FILE} (chmod 600).")
    return k


def clean(text: str, risky: bool) -> str:
    """The text without lines that look private; healthcare text also loses anything patient-like."""
    out = []
    for line in (text or "").splitlines():
        if privacy.private(line, risky) or (risky and privacy.PHI_TEXT.search(line)):
            continue
        out.append(line.strip())
    return "\n".join(l for l in out if l)


def material(db: DB, pid: int) -> tuple:
    """(text to send, documents used) for one portfolio project."""
    rows = [dict(r) for r in db.q("SELECT * FROM projects WHERE id=? OR merged_into=?", (pid, pid))]
    health = any(re.search(r"health|medic|clinic|care|pharm|lab|patient|genetic", f"{r['industry']} {r['name']}", re.I) for r in rows)
    parts, units = [], set()
    for r in rows:
        bits = [f"Entry: {r['name']}"] + [f"{k}: {r[k]}" for k in ("client_name", "client_country", "industry", "years", "loc_role",
                                                                   "summary", "problem", "solution", "results", "tech", "domains")
                                          if r.get(k) and r[k] not in ("[]", "{}")]
        parts.append(clean("\n".join(bits), health))
        units |= set(json.loads(r["units"] or "[]"))
    docs = {x["source"] for x in db.q("SELECT DISTINCT source FROM pictures WHERE project_id=? AND status IN ('kept','review','approved')", (pid,))}
    if units:
        q = ",".join("?" * len(units))
        docs |= {x["path"] for x in db.q(f"SELECT path FROM sources WHERE unit_id IN ({q}) AND status!='sensitive'", tuple(units))}
    sensitive = {x["path"] for x in db.q("SELECT path FROM sources WHERE status='sensitive'")}
    docs = [d for d in docs if d not in sensitive and find.kind_of(d) in ("docx", "pptx", "xlsx", "pdf") and os.path.exists(d)]
    # a document counts only when it is about this project: not inside a folder named after another project, and its path
    # or text names the project or its client (a picture filed here by mistake must not bring another project's story)
    from .candidates import FOLDER_GENERIC, Projects
    P = Projects(db)
    # only words that name few projects (acme, northwind, helios...), never words every catalogue entry shares
    spread: dict = {}
    for r in db.q("SELECT id, merged_into, name, client_name FROM projects"):
        for w in set(re.findall(r"[a-z0-9]{3,}", f"{r['name']} {r['client_name'] or ''}".lower())):
            spread.setdefault(w, set()).add(r["merged_into"] or r["id"])
    words = set()
    for r in rows:
        words |= set(re.findall(r"[a-z0-9]{3,}", f"{r['name']} {r['client_name'] or ''}".lower()))
    words = {w for w in words - FOLDER_GENERIC if len(spread.get(w, ())) <= 2 and not w.isdigit()}
    about = re.compile(r"\b(" + "|".join(map(re.escape, sorted(words, key=len, reverse=True))) + r")\b", re.I) if words else None
    docs = [d for d in docs if P.named_by_folder(d) in (None, pid)]
    docs.sort(key=lambda d: (not GOOD.search(os.path.basename(d)), os.path.basename(d).lower()))
    used, total = [], sum(map(len, parts))
    for d in docs[:MAX_DOCS * 4]:
        if len(used) >= MAX_DOCS or total >= ALL_CHARS:
            break
        try:
            t = clean(find.doc_text(d, DOC_CHARS * 2), health)[:DOC_CHARS]
        except Exception:
            continue
        # its folder is named after this project, or its path names the project; the text alone does not count (job sheets
        # and planning sheets mention many projects)
        if len(t) < 80 or not (P.named_by_folder(d) == pid or (about and about.search(os.path.relpath(d, config.ARCHIVE)))):
            continue
        rel = os.path.relpath(d, config.ARCHIVE)
        parts.append(f"--- Document: {rel}\n{t}")
        used.append(rel)
        total += len(t)
    return "\n\n".join(parts)[:ALL_CHARS + 4000], used


# free models are rate limited: when one stays busy the next is tried (VAULT_LLM_FALLBACK, comma separated)
FALLBACK = [m for m in os.environ.get("VAULT_LLM_FALLBACK", "google/gemma-4-31b-it:free,google/gemma-4-26b-a4b-it:free,"
                                      "nvidia/nemotron-3-ultra-550b-a55b:free").split(",") if m]


def _call(model: str, text: str, key: str, prompt: str = "") -> dict:
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": (prompt or PROMPT).replace("AGENCY", config.COMPANY or "the agency") + text}],
                       "temperature": 0.2}).encode()
    req = urllib.request.Request(URL, body, {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                             "X-Title": "My Projects Vault"})
    with urllib.request.urlopen(req, timeout=180) as r:
        out = json.load(r)
    msg = out["choices"][0]["message"]["content"] or ""
    m = re.search(r"\{.*\}", msg, re.S)   # the JSON object, without any ``` fences or words around it
    return {"details": json.loads(m.group(0) if m else msg), "usage": out.get("usage", {}), "model": out.get("model", model)}


REFINE = """You edit portfolio case-study notes. Below are notes about one software product delivered by an agency. Rewrite only "problems" and "solution" at the level of the product, for a reader deciding whether to hire the team:
- "problems": at most 5 business or user problems the product solves: what was hard, slow or missing for these users
  or for the client's business before the product existed. Derive them from the overview, the users and what the product
  does. Not bugs, QA issues, tickets or missing features found during development.
- "solution": at most 6 items on how the product solves them for its users (capabilities), not bug fixes or task lists.
Keep every statement supported by the notes; do not invent numbers or facts. Return JSON only:
{"problems": [...], "solution": [...]}

NOTES
"""


def refine(d: dict, key: str, log=print) -> dict:
    """Second pass without the documents: problems and solution rewritten at the product level."""
    # the first draft's problems are left out on purpose: drawn from bug lists they pull the rewrite back to bugs
    notes = {k: d.get(k) for k in ("overview", "users", "key_features", "platforms", "solution")}
    try:
        r = _call(MODEL, json.dumps(notes, ensure_ascii=False), key, prompt=REFINE)
        out = r["details"]
        if isinstance(out.get("problems"), list) and isinstance(out.get("solution"), list) and out["problems"]:
            d["problems"], d["solution"] = out["problems"][:5], out["solution"][:6]
        d["_refine_tokens"] = int(r["usage"].get("total_tokens") or 0)
    except Exception as e:
        log(f"  refine skipped: {e}")
    d["problems"], d["solution"] = (d.get("problems") or [])[:5], (d.get("solution") or [])[:6]
    return d


class DailyLimit(Exception):
    """OpenRouter's daily allowance of free requests is used up: stop and carry on another day."""


def ask(text: str, key: str, log=print) -> dict:
    """Ask MODEL; on 429 (busy) or 5xx wait and retry, then move on to the fallback models."""
    import urllib.error
    models = list(dict.fromkeys([MODEL] + (FALLBACK if MODEL.endswith(":free") else [])))
    last = None
    for model in models:
        for attempt in range(4):
            try:
                return _call(model, text, key)
            except urllib.error.HTTPError as e:
                last = e
                body = ""
                try:
                    body = e.read().decode("utf-8", "replace")
                except Exception:
                    pass
                if e.code == 429 and re.search(r"per[- ]day|daily|free-models-per-day", body, re.I):
                    raise DailyLimit(body[:200])
                if e.code not in (429, 500, 502, 503, 504):
                    raise
                wait = int(e.headers.get("Retry-After") or 0) or 15 * (attempt + 1)
                log(f"  {model} busy ({e.code}), waiting {wait} s")
                time.sleep(min(wait, 90))
            except (json.JSONDecodeError, KeyError, IndexError) as e:
                last = e
                break   # this model did not return JSON: try the next one
    raise RuntimeError(f"no model answered: {last}")


def run(db: DB, only: str = "", redo: bool = False, limit: int = 0, log=print) -> dict:
    key = api_key()
    # projects with the most pictures first: they are the ones the portfolio shows
    rows = db.q("SELECT pr.id, pr.name, pr.details_at, (SELECT COUNT(*) FROM pictures p WHERE p.project_id=pr.id AND p.status IN "
                "('kept','review','approved')) n FROM projects pr WHERE merged_into IS NULL AND status!='hidden' ORDER BY n DESC, name")
    rows = [r for r in rows if only.lower() in r["name"].lower() and (redo or not r["details_at"])]
    if limit:
        rows = rows[:limit]
    done = failed = tokens = 0
    for i, r in enumerate(rows, 1):
        text, used = material(db, r["id"])
        try:
            a = ask(text, key, log)
        except DailyLimit as e:
            log(f"daily limit of free requests reached, stopping: {e}")
            break
        except Exception as e:
            failed += 1
            log(f"{r['name']}: {e}")
            continue
        d = refine(a["details"], key, log)
        tokens += d.pop("_refine_tokens", 0)
        d["documents"] = used
        tokens += int(a["usage"].get("total_tokens") or 0)
        db.x("UPDATE projects SET details=?, details_at=?, details_model=? WHERE id=?",
             (json.dumps(d, ensure_ascii=False), now(), a["model"], r["id"]))
        done += 1
        log(f"{i}/{len(rows)} {r['name'][:50]}")
        time.sleep(0.3)
    return {"written": done, "failed": failed, "tokens": tokens, "model": MODEL}
