"""Step 1: every picture in the archive worth a look: pictures inside Word, PowerPoint and Excel files, PDF pages that are mostly a
picture, and image files. Meeting notes and recordings, sensitive files and decoration folders are never opened."""
from __future__ import annotations

import os
import re
import zipfile

from . import config, privacy, vision

DOCS = {"docx", "pptx", "xlsx", "pdf"}
IMGS = {"png", "jpg", "jpeg", "heic", "webp"}
MEDIA = re.compile(r"^(word|ppt|xl)/media/[^/]+\.(png|jpe?g)$", re.I)


def kind_of(path: str) -> str:
    base = os.path.basename(path).lower()
    return base.rsplit(".", 1)[-1] if "." in base else ""


def files(root: str = config.ARCHIVE):
    """Paths of documents and image files that may be opened."""
    blocked = []
    try:
        os.listdir(root)
    except PermissionError:
        raise PermissionError("macOS blocks reading the archive folder: give Terminal (or Python) Full Disk Access.")
    for d, dirs, names in os.walk(root, onerror=blocked.append):
        rel_d = os.path.relpath(d, root)
        dirs[:] = sorted(x for x in dirs if not x.startswith(".") and not privacy.JUNK_DIR.search("/" + os.path.join(rel_d, x) + "/"))
        for f in sorted(names):
            if f.startswith(("._", "~$")) or kind_of(f) not in DOCS | IMGS:
                continue
            rel = os.path.normpath(os.path.join(rel_d, f))
            if privacy.SENSITIVE_PATH.search(rel) or privacy.MEETING.search(rel):
                continue
            yield os.path.join(d, f)


def doc_text(path: str, limit: int = 3000) -> str:
    """The start of a document's own text (slide titles, headings, first paragraphs), read from the file's XML, for deciding
    which project the whole document belongs to. Image files have none."""
    import html as html_mod
    k = kind_of(path)
    try:
        if k in ("docx", "pptx", "xlsx"):
            with zipfile.ZipFile(path) as z:
                names = z.namelist()
                if k == "docx":
                    parts = ["word/document.xml"]
                elif k == "pptx":
                    parts = sorted((n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)), key=lambda n: int(re.findall(r"\d+", n)[-1]))[:15]
                else:
                    parts = ["xl/sharedStrings.xml"] if "xl/sharedStrings.xml" in names else []
                out = []
                for n in parts:
                    xml = z.read(n).decode("utf8", "ignore")
                    xml = re.sub(r"</(w:p|a:p|si)>", "\n", xml)
                    out.append(html_mod.unescape(re.sub(r"<[^>]+>", "", xml)))
                    if sum(map(len, out)) > limit:
                        break
                text = "\n".join(out)
        elif k == "pdf":
            text = vision.pdf_text(path, 4)
        else:
            return ""
    except (OSError, zipfile.BadZipFile, KeyError, ValueError):
        return ""
    return re.sub(r"\s*\n\s*", "\n", re.sub(r"[ \t]+", " ", text)).strip()[:limit]


def quality_ok(w: int, h: int) -> str:
    """'' when the picture is big enough to show, else why not."""
    if not 0.3 <= w / h <= 3.5:
        return "unusual aspect ratio (banner or decoration)"
    if (w >= config.MIN_W and h >= config.MIN_H) or (w >= config.MIN_PHONE_W and h >= config.MIN_PHONE_H):
        return ""
    return "too small"


def pictures(path: str):
    """(member, bytes or None, CGImage or None) for each picture in a file."""
    k = kind_of(path)
    if k in IMGS:
        if config.MIN_BYTES <= os.path.getsize(path) <= config.MAX_BYTES:
            with open(path, "rb") as f:
                yield "", f.read(), None
    elif k in ("docx", "pptx", "xlsx"):
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                if MEDIA.match(info.filename) and config.MIN_BYTES <= info.file_size <= config.MAX_BYTES:
                    yield info.filename, z.read(info), None
    elif k == "pdf":
        for n, img in vision.pdf_pages(path):
            yield f"page {n}", None, img
