"""Where things are and how the run behaves.

Personal settings (the archive path, the agency name, the owner's name, the site folder...) live in `vault.toml`, which is
never committed; copy `vault.example.toml` to start. The file is looked up in $VAULT_CONFIG, then the repo folder, then
~/.config/myprojectsvault/vault.toml. Any setting can also be overridden with the environment variable shown next to it.
"""
from __future__ import annotations

import os
import tomllib

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load() -> dict:
    for p in (os.environ.get("VAULT_CONFIG", ""), os.path.join(_REPO, "vault.toml"),
              os.path.expanduser("~/.config/myprojectsvault/vault.toml")):
        if p and os.path.isfile(p):
            with open(p, "rb") as f:
                return tomllib.load(f)
    return {}


_C = _load()


def _get(section: str, key: str, env: str, default):
    v = os.environ.get(env)
    if v is not None:
        return v
    return _C.get(section, {}).get(key, default)


# the work archive to read (read only)
ARCHIVE = os.path.expanduser(_get("paths", "archive", "VAULT_ARCHIVE", "~/Archive"))
# the database and the picture library: outside the repo, on this Mac only
DATA = os.path.expanduser(_get("paths", "data", "VAULT_DATA", "~/Library/Application Support/MyProjectsVault"))
DB_PATH = os.path.join(DATA, "portfolio.db")
PICTURES = os.path.join(DATA, "pictures")
# an older catalogue whose projects can be copied in once as a starting point (opened read only); optional
SEED_DB = os.path.expanduser(_get("paths", "seed_db", "VAULT_SEED", ""))

# the portfolio: who built it and where client work was delivered
OWNER = _get("portfolio", "owner", "VAULT_OWNER", "")                 # shown on the portfolio page and PDF
COMPANY = _get("portfolio", "company", "VAULT_COMPANY", "")           # the agency that delivered client projects
OWNER_PHOTO = _get("portfolio", "photo", "VAULT_PHOTO", "")           # site path of the owner's photo, e.g. /me.jpg
PDF_NAME = _get("portfolio", "pdf_name", "VAULT_PDF_NAME", "Portfolio.pdf")
FOCUS = _get("portfolio", "focus", "VAULT_FOCUS", "")                 # regex of the field to favour among featured projects

# folder-name words that say nothing about the project in your archive (e.g. your company's name at the top of every path)
GENERIC_FOLDERS = [w.lower() for w in _C.get("rules", {}).get("generic_folders", [])]

# tools
CHROME = os.path.expanduser(_get("tools", "chrome", "VAULT_CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))

# JEV (local decision model)
JEV_RELEASE = _get("jev", "release", "VAULT_JEV_RELEASE", "2b")
JEV_BACKEND = _get("jev", "backend", "VAULT_JEV_BACKEND", "mlx")

# picture quality: a screen or a diagram big and sharp enough to show
MIN_W, MIN_H = 900, 500          # landscape screens and diagrams
MIN_PHONE_W, MIN_PHONE_H = 400, 800   # phone screens
MIN_BYTES, MAX_BYTES = 30_000, 20_000_000
MIN_LINES = 8                     # screens and diagrams carry labels, menus and buttons; logos and decoration do not
OUT_W = 1600                      # saved copies are at most this wide (never enlarged)

# pace: after each document, rest REST times as long as the work took (1.0 = about half speed, cooler; 0 = full speed)
REST = float(_get("run", "rest", "VAULT_REST", 1.0))

# covering private text and faces when a picture is saved. Off: pictures are kept as they are (the owner picks the ones
# to cover later); pictures with signs of patient data are still marked 'review' so they are easy to find.
REDACT = str(_get("run", "redact", "VAULT_REDACT", "0")) in ("1", "true", "True")

# decisions
KIND_KEEP = ("screen", "diagram")
JEV_SURE = 0.6                    # JEV probability above which its answer is taken as is
KEEP_SURE = 0.5                   # P(screen) + P(diagram) at or above which a picture is kept (when its project is known)
PROJECT_SURE = 0.5                # JEV probability for a document's project at or above which it is taken
CLAUDE_MODEL = _get("llm", "claude_model", "VAULT_CLAUDE_MODEL", "haiku")
