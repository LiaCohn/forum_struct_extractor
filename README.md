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

Concrete run against a real public forum (the official XenForo community board):

```bash
python forum_extractor_hybrid.py --url https://xenforo.com/community/whats-new/posts --debug
```

This fetches the page with Playwright, detects the thread rows, and prints the JSON selectors plus a preview of the first few extracted rows.

## Local HTML file

```bash
python forum_extractor_hybrid.py --file forum.html
```

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

# Example runs (real output)

Below are **actual** runs with the LLM enabled. Full `xpath` strings on candidates are long; the snippets show `id`, `role_hint`, `tag`, and `text` (see your terminal or use `--show-llm-candidates` for the complete JSON).

## Example 1 — Russian XenForo (`whats_new.html`)

### Command

```bash
cd chat_solutions
python forum_extractor_hybrid.py --file ../whats_new.html --debug --show-llm-candidates
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

### Sample of LLM candidates (abbreviated)

| id  | role_hint            | tag  | text (excerpt) |
|-----|----------------------|------|----------------|
| c1  | date                 | time | 14 Янв 2026 |
| c2  | date                 | time | 5 мин. назад |
| c3  | possible_title_link  | a    | ✅ XMart - Маркетплейс готовых аккаунтов… |
| c4  | possible_author_link | a    | Raccoonstock |
| c5  | possible_title_link  | a    | 14 Янв 2026 (start-date link; same thread URL) |
| c6  | link                 | a    | Прочие продажи и покупки (forum breadcrumb) |

The model picks **c3** for title/link (real thread title), **c4** for author (`username`), **c1** for publish date (`u-dt` on thread start), avoiding breadcrumb / “latest activity” links.

### Preview (first rows)

```json
[
  {
    "title": "✅ XMart - Маркетплейс готовых аккаунтов. Покупка и продажа.",
    "author": "Raccoonstock",
    "publish_date": "14 Янв 2026",
    "link": "/threads/%E2%9C%85-xmart-%D0%9C%D0%B0%D1%80%D0%BA%D0%B5%D1%82%D0%BF%D0%BB%D0%B5%D0%B9%D1%81-%D0%B3%D0%BE%D1%82%D0%BE%D0%B2%D1%8B%D1%85-%D0%B0%D0%BA%D0%BA%D0%B0%D1%83%D0%BD%D1%82%D0%BE%D0%B2-%D0%9F%D0%BE%D0%BA%D1%83%D0%BF%D0%BA%D0%B0-%D0%B8-%D0%BF%D1%80%D0%BE%D0%B4%D0%B0%D0%B6%D0%B0.191889/"
  },
  {
    "title": "Эксклюзивные и VIP тарифы от МТС, Мегафон, Теле2, Билайн",
    "author": "Omark",
    "publish_date": "8 Ноя 2025",
    "link": "/threads/%D0%AD%D0%BA%D1%81%D0%BA%D0%BB%D1%8E%D0%B7%D0%B8%D0%B2%D0%BD%D1%8B%D0%B5-%D0%B8-vip-%D1%82%D0%B0%D1%80%D0%B8%D1%84%D1%8B-%D0%BE%D1%82-%D0%9C%D0%A2%D0%A1-%D0%9C%D0%B5%D0%B3%D0%B0%D1%84%D0%BE%D0%BD-%D0%A2%D0%B5%D0%BB%D0%B52-%D0%91%D0%B8%D0%BB%D0%B0%D0%B9%D0%BD.178542/"
  },
  {
    "title": "Заказать/купить рассылку Whatsap | SMS | Viber | Telegram. Инвайт в Telegram и Viber",
    "author": "Raccoonstock",
    "publish_date": "16 Фев 2025",
    "link": "/threads/%D0%97%D0%B0%D0%BA%D0%B0%D0%B7%D0%B0%D1%82%D1%8C-%D0%BA%D1%83%D0%BF%D0%B8%D1%82%D1%8C-%D1%80%D0%B0%D1%81%D1%81%D1%8B%D0%BB%D0%BA%D1%83-whatsap-sms-viber-telegram-%D0%98%D0%BD%D0%B2%D0%B0%D0%B9%D1%82-%D0%B2-telegram-%D0%B8-viber.136287/"
  }
]
```

---

## Example 2 — XDA (deterministic only)

For comparison, with the LLM off (same final XPaths on a standard XenForo row, no candidate dump):

```bash
python forum_extractor_hybrid.py --file ../xda_posts.html --no-llm --debug
```

Preview rows include real thread titles and `/t/...` links (see your saved `xda_posts.html` fixture).

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
