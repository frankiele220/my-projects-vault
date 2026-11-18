# How My Projects Vault works

This page explains the pipeline, how the local decision model (JEV) is used, what was measured, and what was learned on a
real archive of about 11,000 files.

## The pipeline

| Step | Command | What happens | Where it runs |
|---|---|---|---|
| 1. Index | `vault index` | Walks the archive, keeps images, videos, Word, PowerPoint, Excel and PDF files, skips junk and sensitive paths, and gives each file a priority (0 to 100) from its type, name and how many pictures it holds. Nothing is opened or decoded. | Mac |
| 2. Extract | `vault run` | Pulls every picture out of the documents (embedded media in Office files, mostly-picture pages in PDFs), drops small or odd-shaped ones, and reads the text in each picture with Apple Vision. | Mac |
| 3. Decide | `vault run` | Asks JEV what kind of picture it is (screen, diagram, slide, document page, asset, photo) and which project the document belongs to. | Mac |
| 4. Organize | `vault run`, `vault categorize`, `vault annotations` | Keeps screens and diagrams, puts them in a project folder and a group (website, web app, mobile app, admin dashboard, store assets, diagram), and sets bug-report screenshots aside. | Mac |
| 5. Write | `vault details` | A writing model drafts a short case study per project from the project's own documents. | OpenRouter |
| 6. Publish | `vault export`, `vault publish` | Builds the portfolio page and the PDF from the pictures the owner marked "can use in public". | Mac |

A reader thread extracts and reads pictures while the main thread asks JEV, so the model never waits for Vision. A file is
only marked done once all of its pictures are recorded, so a stopped run carries on where it left off.

## JEV, a decision model

JEV is a **decision model**: you give it a piece of text (the `state`) and questions with fixed answers. It does not write
text; it returns a **probability for each answer**. For example, "What kind of picture is this?" with six answers returns
`screen 0.81, diagram 0.07, slide 0.05, ...`, and you decide the threshold.

The project uses [`jev-style`](https://pypi.org/project/jev-style/) (Apache-2.0), with the 2B release running through MLX on
Apple Silicon. Nothing leaves the Mac, so it can read text that came from pictures holding private data.

### Three kinds of questions

| Kind | Use it for | JEV returns |
|---|---|---|
| `choice` | one answer out of 1 to 255 | the pick, a probability per answer, a confidence |
| `noul` | yes or no | the probability of yes |
| `score` | a scale of 2 to 10 steps | the expected level and a probability per level |

### Example (from `myprojectsvault/judge.py`)

```python
from jev_style import JevStyle, choice

js = JevStyle(backend="mlx", release="2b")      # load once, reuse

state = """Picture taken from the document "Claims portal - release notes.pptx" in the folder "Billing/Claims".
Size: 1600 x 900 pixels, 10 lines of text.
Text read from the picture:
Claims / Codes / Rules / Primary insurance / ..."""

answer = js.decide(state, {
    "kind": choice("What kind of picture is this?", {
        "screen":  "A screenshot of a software application, website, mobile app, admin panel or dashboard",
        "diagram": "Architecture, data flow, workflow, database model, sitemap, wireframe or org chart",
        "asset":   "A logo, icon, banner, background or decorative graphic",
    }),
})
answer["answers"]["kind"]     # {"choice": "screen", "probabilities": {...}, "confidence": ...}
```

One `decide` call can ask several questions about the same `state`, which is faster than asking them one by one.

### Tips that made JEV answer well

1. **Describe every answer.** `"screen": "A screenshot of a software application..."` works far better than `"screen"`.
2. **Offer a "none of these" answer** when the right answer may be missing, or JEV has to pick something.
3. **Fewer answers, better answers.** The project never offers every project; rules gather at most 25 candidates first.
4. **Give it clues.** The state holds the full folder path level by level, the file name, the picture size, a summary of
   how the text is laid out (many short labels look like a user interface, long lines look like a document page) and the
   text itself.
5. **Set a threshold and a fallback.** Below it, the picture is marked `unsure` for a later pass or for a person to check.
6. **Measure on your own data.** Classify about 50 pictures by hand and compare.

### Measured on an M4 Mac with 16 GB

| Step | Time | How often |
|---|---|---|
| Load the 2B model (MLX) | about 7 s | once |
| Kind of picture | about 0.2 s | per picture |
| Project (7 candidates) | about 0.6 s | per document |
| Apple Vision text reading | about 0.14 s | per picture |
| Index the archive (about 11,000 files) | about 8 s | once |

## Lessons learned

- **The folder path is the best clue.** Archives are usually organized by client and project ("160103 - Acme v2"), while
  folders such as iOS, iPad, Design or Store Assets only say what kind of file it is. Showing JEV the full path level by
  level, and telling it which folders name projects, fixed most wrong project picks.
- **Rules gather, the model decides.** Early versions let rules assign projects directly, and one project whose name
  contained "iPad" collected every iPad screenshot. Rules now only collect candidates; a folder named exactly after one
  project still wins unless JEV clearly rules it out, because the small model can be right but unsure.
- **Decide the project once per document**, not per picture, and ask each picture only one narrow question. On the same
  files this raised the share of pictures kept from about 12% to about 72%.
- **Text-only is enough for sorting.** An image-reading JEV build exists (torch backend, about 4.4 GB), but on a 16 GB Mac it
  runs in float32 only, needs about 9 GB of memory and swapped the machine to a crawl. Apple Vision plus the text model is
  fast and good enough; a small image model (for example MobileCLIP) is the next step for pictures with little text.
- **Bug-report screenshots are easy to spot by colour.** Thin, pure-red strokes (boxes, circles, underlines from markup
  tools) mark them; any strong colour also catches design lines, so the check uses pure red only.
- **Write case studies at the product level.** When a project's documents are mostly bug lists, a first draft lists bugs
  as "problems". A second, cheap pass without the documents rewrites problems and solution at the product level.

## Privacy model

- Pictures, their text and every decision stay on the Mac.
- The case-study writer receives short excerpts of a project's own documents, after every line that looks private (names,
  e-mail addresses, phone numbers, IDs, dates of birth, addresses) is removed; documents marked sensitive are skipped.
- Pictures with signs of patient data are marked `review`; covering (redaction) can be switched on with `redact = true`.
- Nothing is public by default: only pictures marked "can use in public" reach the portfolio page and the PDF, and the
  export leaves out internal notes such as results.
