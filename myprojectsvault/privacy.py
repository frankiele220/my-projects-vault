"""What is private in a picture, and which files are never opened.

Text read from a picture that looks like private data is covered with black boxes: people's names, dates, phone numbers,
e-mail and web addresses, ID numbers (record, member, Medicare, claim, UUID), street addresses, the value next to a label
such as "DOB" or "Patient", and every face. In pictures with signs of patient data, diagnosis codes and avatar initials too.
"""
from __future__ import annotations

import re

# Never opened: patient data, passwords and keys, mailboxes, personal finance and ID papers.
SENSITIVE_PATH = re.compile(
    r"progress[ _-]?notes?|patient|medical[ _-]?records?|records[ _-]?submission|\bupic\b|\bphi\b|\bmrn\b|lab[ _-]?results?|"
    r"transcripts?\b|transcription|keepass|\.kdbx$|passwords?|credentials?|secrets?\b|\.env$|id_rsa|\.pem$|\.p12$|\.key$|"
    r"payroll|salary|salaries|passport|bank[ _-]?statement|tax[ _-]?return|rebuttal|incident[ _-]?report|"
    r"(^|/)[\w.+-]+@[\w-]+\.[\w.]+(/|$)", re.I)
# Notes and recordings of meetings: their pictures are frames of a compressed video call, too blurry to show.
MEETING = re.compile(r"notes by gemini|google meet|meet recordings?|meeting (notes|recordings?)|transcripts?\b|transcription|"
                     r"\bzoom\b|\botter\b|\bfathom\b|\btl;?dv\b|teams meeting|recording", re.I)
# Folders of fonts, icons, wallpapers, logos and build output: decoration, not work.
JUNK_DIR = re.compile(r"(^|/)(node_modules|vendor|\.git|__pycache__|build|dist|cache|tmp|temp|fonts?|icons?|wallpapers?|"
                      r"logos?|brand(ing)?|business ?cards?|profile ?pics?|videos?|psd)(/|$)", re.I)
# Inside a document: signs of patient data.
PHI_TEXT = re.compile(r"\b(date of birth|d\.o\.b\.?|dob\s*:|mrn\s*:|medical record (number|no)|patient name\s*:|ssn\s*:|"
                      r"social security number|member id\s*:|diagnosis code|icd-?10\s*:)", re.I)

ALWAYS = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.]+"                                             # e-mail
    r"|\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b"                               # phone (US style)
    r"|\+\d{1,3}[\s.-]?\d[\d\s.-]{7,}\d"                                    # phone (international)
    r"|\b\d{3}-\d{2}-\d{4}\b"                                              # SSN
    r"|\b\d{1,2}[/.-]\d{1,2}[/.-](19|20)?\d{2}\b"                          # 01/02/1980
    r"|\b(19|20)\d{2}-\d{2}-\d{2}\b"                                       # 1980-01-02
    r"|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+(19|20)\d{2}\b"
    r"|\b[A-Z]{0,3}\d{6,}\b"                                               # record, member and account numbers
    r"|\b(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{8,}\b"                  # mixed IDs such as a Medicare number
    r"|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"    # UUIDs (record addresses)
    r"|https?://|\bwww\.|\b[\w-]+\.(com|net|org|io|app|health|co|us|ai|dev|vn)(/|\b)"   # web addresses
    r"|\b\d{1,6}\s+[A-Za-z0-9.' ]{2,40}\b(street|st|avenue|ave|road|rd|boulevard|blvd|drive|dr|lane|ln|way|court|ct|place|pl|"
    r"suite|ste|highway|hwy|parkway|pkwy)\b\.?", re.I)
LABEL = re.compile(
    r"\b(patient|client name|full name|first name|last name|name\s*:|dob|d\.o\.b|date of birth|birth ?date|mrn|medical record|"
    r"ssn|social security|member ?id|subscriber|policy ?(no|number|#)|insurance ?id|address|phone|mobile|e-?mail|dx|diagnosis|"
    r"icd|npi|guardian|caregiver|emergency contact|claim ?(id|no|number|#)?|mbi|hicn|account ?(no|number|#)|"
    r"encounter ?(id|#)|order ?(id|#)|accession|specimen ?(id|#)?|case ?(id|#))(?!\w)", re.I)   # "#" ends a label too
RISKY_ONLY = re.compile(r"\b[A-TV-Z]\d{2}(\.\d{1,4})?\b|^[A-Z]{2}$")   # diagnosis codes, avatar initials
NAME = re.compile(r"^(dr\.?\s+)?[A-Z][a-z'’-]+(\s+[A-Z]\.?)?(\s+[A-Z][a-z'’-]+){1,2}$|^[A-Z][a-z'’-]+,\s*[A-Z][a-z'’-]+$")
DR = re.compile(r"\b(dr|mr|mrs|ms|miss)\.?\s+[A-Z][a-z]+", re.I)
UI_WORDS = set("""
add all and api app apply approve archive back billing board calendar call cancel care case cases chat check claim claims
clinic close code company confirm consent contact create dashboard data date delete details device devices doctor document
documents done download draft edit eligibility email enter export file files filter form forms group health help history
home import inbox info insurance invoice invoices lab labs list log login logout management manager medical medication
menu message messages module monitoring new next note notes notification notifications open order orders overview page
patient patients payment payments pending plan portal practice prescription prescriptions preview print profile program
programs provider providers record records referral report reports request requests results review save schedule search
select send service services settings sign start status submit summary support system task tasks team template test
tests total update upload user users view visit visits wellness workflow
""".split())


def _common_words() -> set:
    """Lower-case English words from the macOS word list (proper names there are capitalised, so they are left out)."""
    try:
        with open("/usr/share/dict/words", encoding="utf-8", errors="ignore") as f:
            return {w.strip() for w in f if w[:1].islower()}
    except OSError:
        return set()


COMMON = _common_words()


def private(line: str, risky: bool) -> bool:
    t = line.strip()
    if not t:
        return False
    if ALWAYS.search(t) or LABEL.search(t) or DR.search(t):
        return True
    if risky and RISKY_ONLY.search(t):
        return True
    if NAME.match(t):
        words = [w.lower().strip(".,’'") for w in t.split()]
        if all(w in UI_WORDS for w in words):
            return False
        return risky or not all(w in COMMON for w in words)
    return False


def boxes(lines: list, faces: list, risky: bool) -> list:
    """Boxes to cover: private lines, the value right of or below a label, and faces (padded a little)."""
    out = []
    for i, (t, b) in enumerate(lines):
        if not private(t, risky):
            continue
        out.append(b)
        if LABEL.search(t):   # a form: "Patient" on one line, the name in the next box to the right or just below
            x, y, w, h = b
            for _t2, b2 in lines[i + 1:i + 4]:
                x2, y2, _w2, h2 = b2
                same_row = abs((y2 + h2 / 2) - (y + h / 2)) < h and x2 >= x
                below = 0 < y - y2 < 2.5 * h and abs(x2 - x) < 0.25
                if same_row or below:
                    out.append(b2)
    out += [(x - w * 0.15, y - h * 0.15, w * 1.3, h * 1.3) for x, y, w, h in faces]
    return [(x - 0.004, y - 0.004, w + 0.008, h + 0.008) for x, y, w, h in out]


def covered(img, first: list, risky: bool, passes: int = 4) -> tuple:
    """Cover, read the covered copy again, cover what still looks private, until nothing is left.
    Returns (covered image, all boxes, clean)."""
    from .vision import draw, read
    bx = list(first)
    out = draw(img, bx)
    for _ in range(passes):
        lines, faces = read(out)
        more = boxes(lines, faces, risky)
        if not more:
            return out, bx, True
        bx += more
        out = draw(img, bx)
    lines, faces = read(out)
    return out, bx, not boxes(lines, faces, risky)
