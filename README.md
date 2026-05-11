# Generic Forum Structure Extractor

A Python tool that takes a forum URL (or a local HTML file) and returns reusable XPath selectors for:

- Thread / post title
- Thread link
- Author
- Publish date

The extractor is designed to work across different forum websites without hardcoding URLs or per-site selectors.

---

# Overview

Given a page, `forum_extractor_hybrid.py`:

1. Loads the HTML (Playwright for live URLs, plain read for `--file`).
2. Detects repeated DOM structures that look like forum rows.
3. Climbs from each detected node to the nearest meaningful row parent (e.g. `structItem--thread`), so it doesn't pick an inner cell or an outer page wrapper, and dedupes the result.
4. Builds a shared container XPath using the class names common to those rows.
5. Pre-extracts a compact list of candidate elements from one row, each with a unique id, role hint, relative XPath, classes, text, and (for links) `href` / `datetime`.
6. Asks Groq (LLM) to pick **candidate IDs**, not XPaths, for title / link / author / publish_date. The code then converts the chosen IDs into final XPaths.
7. If the LLM is disabled, errors, or returns an unusable choice, a deterministic heuristic fallback builds the selectors instead.
8. Validates the chosen selectors on real rows. If validation fails, falls back to the heuristic and re-validates.
9. Simplifies every XPath by shortening it as long as the output values stay the same.

The output is a single JSON object with five XPath strings.

---

# Strategy

1. **Find repeating rows** — Scan the DOM for blocks that look like a list item (text + links + date signal), group by structure, score the best group.
2. **Narrow to the real row** — Walk up from each hit to a parent that looks like a thread row (`structItem`, `thread`, …), not a page wrapper; dedupe.
3. **Build one container XPath** — Use classes shared across the first few rows so the XPath matches every row.
4. **Fixed menu for the LLM** — From one row, collect ~18 candidates (times first, then links with hints like “possible title”, then other text). Each has an id (`c1`, `c2`, …). The model only **picks ids**; it does not write XPaths, so it cannot hallucinate selectors.
5. **Check it works** — Run the chosen XPaths on several rows; if something fails, use the deterministic fallback.
6. **Shorten safely** — Try shorter XPaths and keep them only if they return the **same values** as before.

That is the hybrid: **structure is deterministic**, **field disambiguation is LLM-assisted but constrained**.

---

# Features

- Repeated-structure detection (generic, not site-specific).
- LLM-assisted selector inference via Groq: the model only **classifies pre-extracted candidate IDs**, so it cannot invent XPaths.
- Deterministic fallback used when the LLM fails, is disabled, or returns invalid selectors.
- Selector validation against multiple rows.
- Output-preserving XPath simplification.
- Live URL crawling via Playwright, or local HTML file mode.
- Date detection in English plus common Cyrillic month abbreviations (`янв`, `фев`, ..., `дек`).
- Debug preview that runs the selectors and prints sample rows.
- Optional `--show-llm-candidates` to inspect the exact compact candidates and LLM choice.

---

# Installation

```bash
pip install playwright lxml groq python-dotenv
playwright install chromium
```

Optional `.env` file:

```env
GROQ_API_KEY=your_api_key
```

`GROQ_API_KEY3` is also accepted as a fallback. If no API key is available, run with `--no-llm`.

---

# Usage

## Live website (LLM enabled by default)

```bash
python forum_extractor_hybrid.py --url https://example.com/forum
```

This fetches the page with Playwright, detects the thread rows, and prints the JSON selectors.

### Reference URLs from the brief

Two real forum URLs the extractor was tested against:

```bash
# Works as a live URL — Altenen serves the rendered HTML directly.
python forum_extractor_hybrid.py --url https://altenens.is/whats-new/posts --debug

# This board is behind a Cloudflare JS challenge, so headless Playwright
# gets the "Just a moment..." page instead of the forum. The supported
# workflow is to save the rendered HTML from a real browser, then run:
python forum_extractor_hybrid.py --file whats_new.html --debug
```

## Local HTML file

```bash
python forum_extractor_hybrid.py --file forum.html
```

This is the recommended workflow for any page behind anti-bot protection (Cloudflare, BunnyCDN Shield, etc.): open the page in a real browser, save the rendered HTML, then point the extractor at the file.

## Disable the LLM (deterministic only)

```bash
python forum_extractor_hybrid.py --file forum.html --no-llm
```

## Debug mode (also prints a preview of extracted rows)

```bash
python forum_extractor_hybrid.py --file forum.html --debug
```

## Inspect the candidates sent to the LLM (and the chosen IDs)

```bash
python forum_extractor_hybrid.py --file forum.html --show-llm-candidates
```

Combine `--debug` and `--show-llm-candidates` to print selectors, candidate list, LLM choice, and preview in one run.

---

# Example run (real output)

Below is an **actual** live run against the brief's Altenen URL with the LLM enabled. Some thread titles on the source forum contain adult content, so candidate texts and preview titles are **redacted with `[redacted]`** below — the *structure* of the output is unchanged.

### Command

```bash
python forum_extractor_hybrid.py --url https://altenens.is/whats-new/posts --debug --show-llm-candidates
```

### Detected selectors

```json
{
  "container_xpath": "//div[contains(concat(' ', normalize-space(@class), ' '), ' structItem ') and contains(concat(' ', normalize-space(@class), ' '), ' structItem--thread ')]",
  "title_xpath": ".//div[contains(concat(' ', normalize-space(@class), ' '), ' structItem-title ')]/a",
  "link_xpath": ".//div[contains(concat(' ', normalize-space(@class), ' '), ' structItem-title ')]/a/@href",
  "author_xpath": ".//div[contains(concat(' ', normalize-space(@class), ' '), ' structItem-minor ')]/ul[contains(concat(' ', normalize-space(@class), ' '), ' structItem-parts ')]/li[1]/a[contains(concat(' ', normalize-space(@class), ' '), ' username ')]",
  "publish_date_xpath": ".//ul[contains(concat(' ', normalize-space(@class), ' '), ' structItem-parts ')]/li[contains(concat(' ', normalize-space(@class), ' '), ' structItem-startDate ')]/a/time[contains(concat(' ', normalize-space(@class), ' '), ' u-dt ')]"
}
```

### LLM choice (IDs → those XPaths)

```json
{
  "title_id": "c3",
  "link_id": "c3",
  "author_id": "c4",
  "publish_date_id": "c1"
}
```

### Sample of LLM candidates (abbreviated, text redacted)

| id  | role_hint            | tag  | text (excerpt)                                  |
|-----|----------------------|------|-------------------------------------------------|
| c1  | date                 | time | `Today at 2:16 PM` (datetime: `2026-05-11T14:16:26+0100`) |
| c2  | date                 | time | `A moment ago` (latest activity, datetime present)        |
| c3  | possible_title_link  | a    | `[redacted thread title]` (thread URL)          |
| c4  | possible_author_link | a    | `<username>` (class `username`)                 |
| c5  | possible_title_link  | a    | `Today at 2:16 PM` (start-date link, same thread URL) |
| c6  | link                 | a    | `[forum category]` (forum URL)                  |
| c7  | possible_title_link  | a    | `A moment ago` (latest-activity link)           |
| c8  | possible_author_link | a    | `<username>` (latest poster)                    |
| c9  | possible_author_link | a    | `B` (single-letter avatar link)                 |
| c10 | text                 | span | `<username>` (username span inside title link)  |
| c11 | text                 | dt   | `Replies`                                       |
| c12 | text                 | dd   | `41`                                            |

The model picks **c3** for title/link (real thread title — not the start-date link c5, not the latest-activity link c7), **c4** for author (the visible `username` link in the row, not the single-letter avatar c9), and **c1** for publish date (the `u-dt` thread start time, not the “latest activity” time c2).

### Preview (first rows, titles redacted)

```json
[
  {
    "title": "[redacted thread title #1]",
    "author": "<author_1>",
    "publish_date": "Today at 2:16 PM",
    "link": "https://altenens.is/threads/<slug>.2937902/"
  },
  {
    "title": "[redacted thread title #2]",
    "author": "<author_2>",
    "publish_date": "Sep 25, 2025",
    "link": "https://altenens.is/threads/<slug>.2847060/"
  },
  {
    "title": "[redacted thread title #3]",
    "author": "<author_1>",
    "publish_date": "May 30, 2025",
    "link": "https://altenens.is/threads/<slug>.2756312/"
  },
  {
    "title": "[redacted thread title #4]",
    "author": "<author_1>",
    "publish_date": "31 minutes ago",
    "link": "https://altenens.is/threads/<slug>.2937946/"
  },
  {
    "title": "[redacted thread title #5]",
    "author": "<author_3>",
    "publish_date": "Tuesday at 6:06 AM",
    "link": "https://altenens.is/threads/<slug>.2934620/"
  }
]
```

All five rows resolved every field (`title`, `author`, `publish_date`, `link`) — the validator passed without falling back to the deterministic path.

---

# How it works

## 1. DOM loading

`fetch_rendered_html` uses Playwright (`chromium`, `headless=True`, `wait_until="networkidle"`) to get the final post-JS DOM. For offline tests and pages behind anti-bot challenges, `--file` reads a saved HTML file directly.

## 2. Repeated container detection

`find_repeated_containers` walks every element and keeps the ones that look like a forum row (`looks_like_forum_item`):

- non-trivial text length
- at least one `<a href>`
- a `<time>` element, `[@datetime]`, or a date-like keyword in the text (English month names, weekday/duration words, plus Cyrillic month abbreviations)

It buckets candidates by `(tag, sorted_classes)`, scores each bucket by row count, average link count, average text length, and class hints (`thread`, `post`, `item`, `row`, `discussion`, `struct`), and returns the highest-scoring bucket.

## 3. Container refinement

`improve_container_choice` walks each detected node upward until it hits a parent whose class hints at a row (`thread`/`post`/`item`/`row`/`structitem`). Climbing stops at `body`/`html`/`main` or once the parent's text grows past a budget — so it never selects a whole-page wrapper. The result is then **deduplicated** so two originals that climb to the same parent don't dilute the next step.

## 4. Container XPath

`common_container_xpath` takes the classes that appear in all of the first five detected rows and emits an XPath like:

```
//div[contains(... ' structItem ') and contains(... ' structItem--thread ')]
```

with at most two stable class predicates.

## 5. LLM candidate construction

`collect_llm_candidates` pre-extracts a compact set of candidate elements from **one** representative row:

- All `<time>` / `[@datetime]` elements as `date`.
- All `<a href>` elements, classified by class / parent class / href hint as `possible_title_link`, `possible_author_link`, or generic `link`. Avatar-only empty links are skipped.
- Any element with visible text as a `text` candidate.

Each candidate gets a unique id (`c1`, `c2`, …), a `role_hint`, a relative XPath, classes, text, and (for links) `href` + `href_xpath`. The list is capped (default 18) so the prompt stays small.

## 6. LLM selection (when enabled)

`ask_llm_to_choose_candidates` sends Groq (`llama-3.1-8b-instant`) the compact candidate list and asks for `title_id` / `link_id` / `author_id` / `publish_date_id`. Strict JSON, no markdown.

`selectors_from_llm_choice` then converts those IDs into final XPaths deterministically. Because the LLM is only classifying ids that already exist in the DOM, it cannot invent XPaths or hallucinate selectors.

## 7. Deterministic fallback

`fallback_selectors` is used when:

- `--no-llm` is passed,
- the LLM call raises, or
- the LLM choice is incomplete / unusable / fails validation.

It picks the best title link by class / href hints and text length, picks the first `<time>` / `[@datetime]` as the date, and picks the best non-avatar, non-title link with `username` / `user` / `profile`-flavored class or URL as the author.

## 8. Validation

`validate_selectors` checks that every required selector returns at least one node on the first five rows. If not, it swaps the failing selection for the deterministic fallback.

## 9. Simplification

`simplify_xpath_by_validation` shortens each XPath by trying tails of length 1/2/3 of the path. A shorter candidate is kept only if it returns the **same text values** as the original on the sample row — i.e., simplification is verified by behavior, not by guesswork.

---

# Notes

- The test forums are XenForo-based, so they share patterns like `structItem`, `structItem-title`, `username`, `u-dt`. The extraction logic itself is structural, not URL-specific.
- Date detection supports English and common Cyrillic month abbreviations; sites in other languages without `<time>` or `@datetime` may need an extended keyword list.
- For sites behind a JS challenge (Cloudflare, BunnyCDN Shield, etc.), use `--file` after saving the rendered DOM from a real browser.

---

# Future Improvements

- Try multiple container buckets, not just the top-scoring one.
- Soft validation (e.g. ≥80% of rows must populate each field) instead of strict all-or-nothing.
- Broader date detection (more languages, plain numeric date regexes).
- Optional offline cache so the same site is not re-LLMed every run.
