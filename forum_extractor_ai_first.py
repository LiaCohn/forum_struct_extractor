"""
AI-first forum structure extractor.

Companion to forum_extractor_hybrid.py that demonstrates the simplest
possible AI-first design:

  1. Render or load HTML.
  2. Strip obvious noise (scripts, styles, SVG, base64, comments).
  3. Send the cleaned HTML to a single LLM call.
  4. Receive 5 XPaths as JSON.
  5. Validate against the DOM and optionally preview.

There is no repeated-container detection, no candidate enumeration, no
deterministic fallback. The LLM does all the structural reasoning.

This file exists for comparison. It is shorter, simpler, and slower per
request than the hybrid pipeline, and it requires a model that is good
at reasoning over raw HTML (defaults to Groq's llama-3.3-70b-versatile).
"""

import argparse
import json
import os
import re

from dotenv import load_dotenv
from groq import Groq
from lxml import etree, html

from forum_extractor_hybrid import (
    fetch_rendered_html,
    load_html_from_file,
    preview_extraction,
    validate_selectors,
)


load_dotenv()


DEFAULT_MODEL = "llama-3.1-8b-instant"
DEFAULT_MAX_CHARS = 380_000

STRIP_TAGS = {"script", "style", "noscript", "svg", "iframe", "link", "meta", "head"}
KEEP_ATTRS = {"class", "id", "href", "src", "datetime", "name", "type", "role", "title", "value", "alt"}
FIELD_KEYS = ["title_xpath", "link_xpath", "author_xpath", "publish_date_xpath"]


# -----------------------------
# HTML preprocessing
# -----------------------------

def _strip_data_uri(value: str) -> str:
    if value and value.startswith("data:"):
        return "data:..."
    return value


def clean_html_for_llm(page_html: str) -> str:
    """
    Remove things a language model does not need to reason about thread rows:

    - <script>, <style>, <svg>, <noscript>, <iframe>, <link>, <meta>, <head>
    - HTML comments
    - inline `style="..."` and `on*="..."` event handlers
    - data: URIs in `src` / `href` attributes (replaced with "data:...")

    Returns serialized HTML. Structure, tag names, classes, ids, hrefs,
    datetimes, and visible text are all preserved.
    """
    tree = html.fromstring(page_html)

    for comment in tree.xpath("//comment()"):
        parent = comment.getparent()
        if parent is not None:
            parent.remove(comment)

    for tag in STRIP_TAGS:
        for el in tree.xpath(f"//{tag}"):
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)

    for el in tree.iter():
        if not isinstance(el.tag, str):
            continue
        for attr in list(el.attrib):
            if attr not in KEEP_ATTRS:
                del el.attrib[attr]
                continue
            if attr in ("src", "href"):
                el.attrib[attr] = _strip_data_uri(el.attrib[attr])

    cleaned = etree.tostring(tree, encoding="unicode", method="html")
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


# -----------------------------
# LLM call
# -----------------------------

PROMPT_TEMPLATE = """You are an expert at analyzing forum HTML and producing XPath selectors.

The page below is a forum thread list (a "what's new" / latest / index page).
Return ONE container XPath that selects every repeating thread row, and FOUR
relative XPaths for the fields inside one such row.

Rules:
- "container_xpath" is ABSOLUTE (starts with "//").
- The other four are RELATIVE to a container element (start with ".//").
- "link_xpath" must end with "/@href" so it yields the URL string.
- Selectors must work for EVERY row in the list, not a single specific row.
- Prefer class-based predicates over positional indexes.
- Return ONLY JSON, no markdown, no commentary.

Output JSON schema (exact keys):
{{
  "container_xpath": "...",
  "title_xpath": "...",
  "link_xpath": "...",
  "author_xpath": "...",
  "publish_date_xpath": "..."
}}

Source URL (for reference, not authoritative): {base_url}

HTML:
{html}
"""


def build_prompt(cleaned_html: str, base_url: str) -> str:
    return PROMPT_TEMPLATE.format(html=cleaned_html, base_url=base_url or "(none)")


def call_llm(prompt: str, model: str) -> dict:
    """
    One Groq call. Tries GROQ_API_KEY, GROQ_API_KEY2, GROQ_API_KEY3 in order
    so a rate-limited key transparently rolls over.
    """
    keys = [os.getenv(name) for name in ("GROQ_API_KEY", "GROQ_API_KEY2", "GROQ_API_KEY3")]
    keys = [k for k in keys if k]

    if not keys:
        raise RuntimeError("No GROQ_API_KEY* found in environment.")

    last_err = None
    for key in keys:
        try:
            client = Groq(api_key=key)
            completion = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            return json.loads(completion.choices[0].message.content)
        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(f"All Groq keys failed. Last error: {last_err}")


# -----------------------------
# Pipeline
# -----------------------------

def extract_ai_first(
    url: str = None,
    file_path: str = None,
    model: str = DEFAULT_MODEL,
    max_chars: int = DEFAULT_MAX_CHARS,
    return_extra: bool = False,
):
    if file_path:
        page_html = load_html_from_file(file_path)
        base_url = ""
    elif url:
        page_html = fetch_rendered_html(url)
        base_url = url
    else:
        raise ValueError("Provide --url or --file")

    raw_chars = len(page_html)
    cleaned = clean_html_for_llm(page_html)
    cleaned_chars = len(cleaned)

    truncated = False
    if cleaned_chars > max_chars:
        cleaned = cleaned[:max_chars]
        truncated = True

    prompt = build_prompt(cleaned, base_url)
    result = call_llm(prompt, model)

    container_xpath = result.get("container_xpath") or "//*[false()]"
    field_selectors = {k: result.get(k, "") for k in FIELD_KEYS}

    warnings = []
    missing = [k for k in ["container_xpath", *FIELD_KEYS] if not result.get(k)]
    if missing:
        warnings.append(f"missing keys: {', '.join(missing)}")

    tree = html.fromstring(page_html)
    if not validate_selectors(tree, container_xpath, field_selectors):
        warnings.append("validation failed (some XPaths returned no nodes on sample rows)")

    output = {
        "container_xpath": result.get("container_xpath", ""),
        "title_xpath": result.get("title_xpath", ""),
        "link_xpath": result.get("link_xpath", ""),
        "author_xpath": result.get("author_xpath", ""),
        "publish_date_xpath": result.get("publish_date_xpath", ""),
    }
    if warnings:
        output["_warning"] = "; ".join(warnings)

    extra = {
        "base_url": base_url,
        "raw_html_chars": raw_chars,
        "cleaned_html_chars": cleaned_chars,
        "truncated": truncated,
        "model": model,
        "prompt_chars": len(prompt),
    }

    if return_extra:
        return output, tree, extra
    return output


# -----------------------------
# CLI
# -----------------------------

def main():
    parser = argparse.ArgumentParser(description="AI-first forum structure extractor")
    parser.add_argument("--url", help="Forum URL to analyze")
    parser.add_argument("--file", help="Local HTML file to analyze")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Groq model (default: {DEFAULT_MODEL})")
    parser.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS,
                        help=f"Cleaned-HTML truncation budget (default: {DEFAULT_MAX_CHARS})")
    parser.add_argument("--debug", action="store_true",
                        help="Print sizing info and a preview of extracted rows")
    args = parser.parse_args()

    if not args.url and not args.file:
        parser.error("You must provide either --url or --file")

    result, tree, extra = extract_ai_first(
        url=args.url,
        file_path=args.file,
        model=args.model,
        max_chars=args.max_chars,
        return_extra=True,
    )

    print(json.dumps(result, indent=2, ensure_ascii=False))

    if args.debug:
        sizing = {
            "raw_html_chars": extra["raw_html_chars"],
            "cleaned_html_chars": extra["cleaned_html_chars"],
            "compression_ratio": round(extra["cleaned_html_chars"] / max(extra["raw_html_chars"], 1), 3),
            "truncated": extra["truncated"],
            "model": extra["model"],
            "prompt_chars": extra["prompt_chars"],
        }
        print("\nSizing:")
        print(json.dumps(sizing, indent=2))

        print("\nPreview:")
        print(json.dumps(
            preview_extraction(tree, result, base_url=extra["base_url"]),
            indent=2,
            ensure_ascii=False,
        ))


if __name__ == "__main__":
    main()
