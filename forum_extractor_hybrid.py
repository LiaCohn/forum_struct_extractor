import argparse
import json
import os
import re
from collections import defaultdict
from urllib.parse import urljoin

from dotenv import load_dotenv
from groq import Groq
from lxml import html
from playwright.sync_api import sync_playwright


load_dotenv()


# -----------------------------
# HTML loading
# -----------------------------

def fetch_rendered_html(url: str) -> str:
    """Render a live page with Playwright and return final HTML."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=60000)
        content = page.content()
        browser.close()
        return content


def load_html_from_file(file_path: str) -> str:
    """Load HTML from a local file."""
    with open(file_path, "r", encoding="utf-8") as f:
        return f.read()


# -----------------------------
# Generic helpers
# -----------------------------

def short_text(text: str, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:limit]


def get_classes(el):
    return (el.get("class") or "").split()


def class_contains_xpath(class_name: str) -> str:
    """Safe XPath class matcher."""
    return f"contains(concat(' ', normalize-space(@class), ' '), ' {class_name} ')"


def tree_path_key(el):
    """Unique-ish path used for removing duplicate elements."""
    path = []
    current = el

    while current is not None:
        parent = current.getparent()
        if parent is None:
            break

        path.append(str(parent.index(current)))
        current = parent

    return "/".join(reversed(path))


def build_relative_xpath(el, root):
    """
    Build a readable relative XPath from root to element.
    Prefer class-based paths when possible.
    """
    parts = []
    current = el

    while current is not None and current is not root:
        tag = current.tag
        classes = get_classes(current)

        if classes:
            # Ignore very generic utility classes where possible.
            useful = [
                c for c in classes
                if not re.match(r"^(js-|is-|has-|u-|p-|m-|mt|mb|ml|mr)$", c)
            ]
            chosen = useful[:2] or classes[:1]
            predicates = " and ".join(class_contains_xpath(c) for c in chosen)
            part = f"{tag}[{predicates}]"
        else:
            parent = current.getparent()
            if parent is not None:
                same_tag_siblings = [child for child in parent if child.tag == current.tag]
                if len(same_tag_siblings) > 1:
                    part = f"{tag}[{same_tag_siblings.index(current) + 1}]"
                else:
                    part = tag
            else:
                part = tag

        parts.append(part)
        current = current.getparent()

    parts.reverse()
    return ".//" + "/".join(parts)


def node_value(node, xpath=None, base_url=None):
    """Convert XPath result node/string to a clean value."""
    if isinstance(node, str):
        value = short_text(node, 300)
        if xpath and xpath.endswith("/@href") and base_url:
            return urljoin(base_url, value)
        return value

    return short_text(node.text_content(), 300)


def value_from_xpath(container, xpath, base_url=None):
    results = container.xpath(xpath)
    if not results:
        return None

    first = results[0]

    if isinstance(first, str):
        value = short_text(first, 300)
        if xpath.endswith("/@href") and base_url:
            return urljoin(base_url, value)
        return value

    return short_text(first.text_content(), 300)


# -----------------------------
# Step 1: repeated container detection
# -----------------------------

def candidate_signature(el):
    """Group similar elements by tag and class list."""
    return el.tag, tuple(sorted(get_classes(el)))


def looks_like_forum_item(el) -> bool:
    """
    A rough filter for elements that may represent one forum row.
    This is intentionally generic: text + link + date-like signal.
    """
    text = short_text(el.text_content(), 500)
    links = el.xpath(".//a[@href]")
    times = el.xpath(".//time | .//*[@datetime]")

    if len(text) < 15:
        return False

    if len(links) < 1:
        return False

    date_like_text = bool(
        re.search(
            r"\b("
            r"minute|minutes|min|hour|hours|day|days|week|weeks|month|months|year|years|"
            r"ago|today|yesterday|moment|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|"
            r"янв|фев|мар|апр|май|июн|июл|авг|сен|окт|ноя|дек"
            r")\b",
            text,
            re.IGNORECASE,
        )
    )

    return bool(times or date_like_text)


def find_repeated_containers(tree):
    """Find the best repeated DOM group that resembles forum thread rows."""
    groups = defaultdict(list)

    for el in tree.xpath("//*"):
        if not isinstance(el.tag, str):
            continue

        if looks_like_forum_item(el):
            groups[candidate_signature(el)].append(el)

    candidates = []

    for signature, elements in groups.items():
        if len(elements) < 2:
            continue

        avg_text_len = sum(len(short_text(e.text_content(), 1000)) for e in elements) / len(elements)
        avg_links = sum(len(e.xpath(".//a[@href]")) for e in elements) / len(elements)
        class_text = " ".join(signature[1]).lower()

        score = 0
        score += len(elements) * 3
        score += min(avg_links, 5) * 2

        # Avoid selecting huge page wrappers.
        if avg_text_len > 2500:
            score -= 30

        # These words are not site-specific. They are common row/list hints.
        if any(word in class_text for word in ["thread", "post", "item", "row", "discussion", "struct"]):
            score += 10

        candidates.append((score, elements))

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1] if candidates else []


def improve_container_choice(containers):
    """
    If detection selected an inner cell, move to the nearest meaningful row parent.
    Avoid climbing to body/html/global wrappers.
    """
    improved = []

    for c in containers:
        best = c
        current = c

        while current.getparent() is not None:
            parent = current.getparent()
            cls = " ".join(get_classes(parent)).lower()

            if parent.tag in ["html", "body", "main"]:
                break

            text_len = len(short_text(parent.text_content(), 3000))
            if text_len > 2500:
                break

            if any(word in cls for word in ["thread", "post", "item", "row", "structitem"]):
                best = parent
                break

            current = parent

        improved.append(best)

    unique = []
    seen = set()

    for el in improved:
        key = tree_path_key(el)
        if key not in seen:
            unique.append(el)
            seen.add(key)

    return unique


def common_container_xpath(containers):
    """Create a shared XPath for the repeated row container."""
    first = containers[0]
    tag = first.tag
    classes = get_classes(first)

    common_classes = []
    for cls in classes:
        if all(cls in get_classes(c) for c in containers[:5]):
            common_classes.append(cls)

    if common_classes:
        predicates = " and ".join(class_contains_xpath(c) for c in common_classes[:2])
        return f"//{tag}[{predicates}]"

    return f"//{tag}"


# -----------------------------
# Step 2: candidate extraction for LLM
# -----------------------------

def make_candidate(candidate_id, role_hint, el, container, base_url):
    xpath = build_relative_xpath(el, container)
    text = short_text(el.text_content(), 160)

    item = {
        "id": candidate_id,
        "role_hint": role_hint,
        "xpath": xpath,
        "tag": el.tag,
        "text": text,
        "classes": get_classes(el),
    }

    if el.tag == "a":
        href = el.get("href")
        item["href"] = urljoin(base_url, href) if href else None
        item["href_xpath"] = xpath + "/@href"

    if el.tag == "time" or el.get("datetime"):
        item["datetime"] = el.get("datetime")

    return item


def collect_llm_candidates(container, base_url, max_candidates=18):
    """
    Instead of sending raw HTML to the LLM, send compact candidate elements.
    The LLM chooses candidate IDs, and code converts them into XPaths.
    """
    candidates = []
    seen_xpaths = set()

    def add(role_hint, el):
        if len(candidates) >= max_candidates:
            return

        xpath = build_relative_xpath(el, container)
        if xpath in seen_xpaths:
            return

        seen_xpaths.add(xpath)
        candidate_id = f"c{len(candidates) + 1}"
        candidates.append(make_candidate(candidate_id, role_hint, el, container, base_url))

    # Strong date candidates
    for el in container.xpath(".//time | .//*[@datetime]"):
        add("date", el)

    # Link candidates: title, author, profile, category, etc.
    for a in container.xpath(".//a[@href]"):
        text = short_text(a.text_content(), 160)
        href = a.get("href", "")
        class_text = " ".join(get_classes(a)).lower()
        parent_class_text = " ".join(get_classes(a.getparent())).lower() if a.getparent() is not None else ""

        if not text and not href:
            continue

        if "avatar" in class_text and not text:
            continue

        role_hint = "link"

        combined = f"{class_text} {parent_class_text} {href}".lower()
        if any(w in combined for w in ["title", "thread", "subject"]):
            role_hint = "possible_title_link"
        elif any(w in combined for w in ["user", "username", "member", "profile"]):
            role_hint = "possible_author_link"

        add(role_hint, a)

    # Text candidates: useful for sites where title/author are not direct simple links
    for el in container.xpath(".//*[normalize-space(text())]"):
        if len(candidates) >= max_candidates:
            break

        text = short_text(el.text_content(), 160)
        if len(text) < 2:
            continue

        if el.tag in ["script", "style"]:
            continue

        add("text", el)

    return candidates


def ask_llm_to_choose_candidates(container_xpath, candidates):
    """
    Ask LLM to classify existing candidate IDs only.
    It does not invent XPaths.
    """
    api_key = os.getenv("GROQ_API_KEY") or os.getenv("GROQ_API_KEY3")
    if not api_key:
        raise RuntimeError("Missing GROQ_API_KEY in .env")

    client = Groq(api_key=api_key)

    prompt = f"""
You are identifying fields in a forum thread row.

The repeated container XPath is:
{container_xpath}

Below are compact candidate elements from ONE representative repeated forum row.
Choose candidate IDs for:
- title
- link
- author
- publish_date

Important:
- Return ONLY one strict JSON object.
- Do not return markdown.
- Use only IDs that appear in candidates.
- The title is the main thread/post title.
- The link is usually the same candidate as title if it has href.
- The author is the visible username/profile link, not an avatar/image-only link.
- The publish date is usually a time/datetime element.

Return format:
{{
  "title_id": "c...",
  "link_id": "c...",
  "author_id": "c...",
  "publish_date_id": "c..."
}}

Candidates:
{json.dumps(candidates, ensure_ascii=False, indent=2)}
"""

    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {
                "role": "system",
                "content": "Return only strict JSON. No markdown. No explanation."
            },
            {
                "role": "user",
                "content": prompt
            },
        ],
        temperature=0,
        max_tokens=300,
    )

    content = response.choices[0].message.content.strip()

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            raise
        data = json.loads(match.group(0))

    return data


def selectors_from_llm_choice(choice, candidates):
    """
    Convert chosen candidate IDs into final XPath selectors.
    This keeps XPath generation deterministic.
    """
    by_id = {c["id"]: c for c in candidates}

    title = by_id.get(choice.get("title_id"))
    link = by_id.get(choice.get("link_id"))
    author = by_id.get(choice.get("author_id"))
    date = by_id.get(choice.get("publish_date_id"))

    if not title or not author or not date:
        return None

    title_xpath = title["xpath"]

    if link and link.get("href_xpath"):
        link_xpath = link["href_xpath"]
    elif title.get("href_xpath"):
        link_xpath = title["href_xpath"]
    else:
        link_xpath = title_xpath + "/@href"

    return {
        "title_xpath": title_xpath,
        "link_xpath": link_xpath,
        "author_xpath": author["xpath"],
        "publish_date_xpath": date["xpath"],
    }


# -----------------------------
# Step 3: heuristic fallback
# -----------------------------

def fallback_selectors(containers):
    """
    Deterministic non-LLM selector inference.
    Used when --no-llm is passed or the LLM fails.
    """
    first = containers[0]

    # Title = strongest meaningful link
    title_candidates = []

    for a in first.xpath(".//a[@href]"):
        text = short_text(a.text_content())
        href = a.get("href", "")

        if len(text) < 8:
            continue

        class_text = " ".join(get_classes(a)).lower()
        parent_class_text = " ".join(get_classes(a.getparent())).lower() if a.getparent() is not None else ""
        combined = f"{class_text} {parent_class_text} {href}".lower()

        score = len(text)

        if any(w in combined for w in ["title", "thread", "subject"]):
            score += 100

        if any(w in href.lower() for w in ["thread", "post", "posts", "/t/"]):
            score += 30

        title_candidates.append((score, a))

    title_candidates.sort(key=lambda x: x[0], reverse=True)

    if title_candidates:
        title_el = title_candidates[0][1]
    else:
        all_links = first.xpath(".//a[@href]")
        if not all_links:
            raise RuntimeError("Could not find any links inside detected container.")
        title_el = all_links[0]

    title_xpath = build_relative_xpath(title_el, first)

    # Date = first time/datetime element
    date_els = first.xpath(".//time | .//*[@datetime]")
    date_xpath = build_relative_xpath(date_els[0], first) if date_els else ".//*[contains(text(), 'ago')]"

    # Author = visible username/profile link, not title, not avatar
    author_candidates = []

    for a in first.xpath(".//a[@href]"):
        if a is title_el:
            continue

        text = short_text(a.text_content())
        class_text = " ".join(get_classes(a)).lower()
        href = a.get("href", "").lower()

        if not text:
            continue

        if "avatar" in class_text:
            continue

        if a.xpath(".//img") and len(text) <= 2:
            continue

        score = 0

        if "username" in class_text:
            score += 100

        if "user" in class_text or "member" in href or "user" in href or "profile" in href:
            score += 60

        if 2 <= len(text) <= 40:
            score += 20

        author_candidates.append((score, a))

    author_candidates.sort(key=lambda x: x[0], reverse=True)

    author_xpath = (
        build_relative_xpath(author_candidates[0][1], first)
        if author_candidates
        else "(.//a[@href])[2]"
    )

    return {
        "title_xpath": title_xpath,
        "link_xpath": title_xpath + "/@href",
        "author_xpath": author_xpath,
        "publish_date_xpath": date_xpath,
    }


# -----------------------------
# Step 4: validation and simplification
# -----------------------------

def validate_selectors(tree, container_xpath, selectors):
    containers = tree.xpath(container_xpath)
    if not containers:
        return False

    required = ["title_xpath", "author_xpath", "publish_date_xpath", "link_xpath"]

    for key in required:
        if key not in selectors or not selectors[key]:
            return False

    # Validate on several rows.
    for c in containers[:5]:
        for key in required:
            try:
                result = c.xpath(selectors[key])
            except Exception:
                return False

            if not result:
                return False

    return True


def shorten_xpath(xpath: str):
    candidates = [xpath]

    if xpath.startswith(".//"):
        parts = xpath[3:].split("/")
        for n in range(1, min(4, len(parts)) + 1):
            candidates.append(".//" + "/".join(parts[-n:]))

    if xpath.endswith("/@href"):
        base = xpath[:-6]
        for base_candidate in shorten_xpath(base):
            candidates.append(base_candidate + "/@href")

    return list(dict.fromkeys(candidates))


def xpath_values(container, xpath):
    try:
        result = container.xpath(xpath)
    except Exception:
        return None

    values = []
    for x in result:
        if isinstance(x, str):
            values.append(short_text(x, 300))
        elif hasattr(x, "text_content"):
            values.append(short_text(x.text_content(), 300))
        else:
            values.append(str(x))

    return values


def simplify_xpath_by_validation(container, original_xpath):
    original_values = xpath_values(container, original_xpath)
    if not original_values:
        return original_xpath

    best = original_xpath

    for candidate in shorten_xpath(original_xpath):
        candidate_values = xpath_values(container, candidate)
        if candidate_values == original_values:
            best = candidate

    return best


def simplify_result_by_validation(tree, result):
    containers = tree.xpath(result["container_xpath"])
    if not containers:
        return result

    sample = containers[0]
    simplified = dict(result)

    for key in ["title_xpath", "author_xpath", "publish_date_xpath", "link_xpath"]:
        simplified[key] = simplify_xpath_by_validation(sample, result[key])

    return simplified


# -----------------------------
# Step 5: preview/debug
# -----------------------------

def preview_extraction(tree, result, base_url=None, limit=5):
    containers = tree.xpath(result["container_xpath"])
    records = []

    for c in containers[:limit]:
        records.append({
            "title": value_from_xpath(c, result["title_xpath"], base_url),
            "author": value_from_xpath(c, result["author_xpath"], base_url),
            "publish_date": value_from_xpath(c, result["publish_date_xpath"], base_url),
            "link": value_from_xpath(c, result["link_xpath"], base_url),
        })

    return records


# -----------------------------
# Main extraction pipeline
# -----------------------------

def extract_forum_structure(
    url: str = None,
    file_path: str = None,
    use_llm: bool = True,
    return_tree: bool = False,
):
    if file_path:
        page_html = load_html_from_file(file_path)
        base_url = ""
    elif url:
        page_html = fetch_rendered_html(url)
        base_url = url
    else:
        raise ValueError("You must provide either --url or --file")

    tree = html.fromstring(page_html)

    containers = find_repeated_containers(tree)
    containers = improve_container_choice(containers)

    if not containers:
        raise RuntimeError("Could not detect repeated forum containers.")

    container_xpath = common_container_xpath(containers)

    selectors = None
    llm_candidates = None
    llm_choice = None

    if use_llm:
        try:
            llm_candidates = collect_llm_candidates(containers[0], base_url)
            llm_choice = ask_llm_to_choose_candidates(container_xpath, llm_candidates)
            selectors = selectors_from_llm_choice(llm_choice, llm_candidates)
        except Exception as e:
            print(f"LLM failed, using fallback. Reason: {e}")

    if not selectors:
        selectors = fallback_selectors(containers)

    result = {
        "container_xpath": container_xpath,
        "title_xpath": selectors.get("title_xpath", ".//a[@href]"),
        "link_xpath": selectors.get("link_xpath", selectors.get("title_xpath", ".//a[@href]") + "/@href"),
        "author_xpath": selectors.get("author_xpath", "(.//a[@href])[2]"),
        "publish_date_xpath": selectors.get("publish_date_xpath", ".//time"),
    }

    if not validate_selectors(tree, container_xpath, result):
        fallback = fallback_selectors(containers)
        result.update(fallback)

    result = simplify_result_by_validation(tree, result)

    extra = {
        "base_url": base_url,
        "llm_candidates": llm_candidates,
        "llm_choice": llm_choice,
    }

    if return_tree:
        return result, tree, extra

    return result


def main():
    parser = argparse.ArgumentParser(description="Generic forum structure extractor")
    parser.add_argument("--url", help="Forum URL to analyze")
    parser.add_argument("--file", help="Local HTML file to analyze")
    parser.add_argument("--no-llm", action="store_true", help="Disable Groq LLM candidate classification")
    parser.add_argument("--debug", action="store_true", help="Print extracted preview records")
    parser.add_argument("--show-llm-candidates", action="store_true", help="Print compact candidates sent to LLM")
    args = parser.parse_args()

    if not args.url and not args.file:
        parser.error("You must provide either --url or --file")

    if args.debug or args.show_llm_candidates:
        result, tree, extra = extract_forum_structure(
            url=args.url,
            file_path=args.file,
            use_llm=not args.no_llm,
            return_tree=True,
        )

        print(json.dumps(result, indent=2, ensure_ascii=False))

        if args.show_llm_candidates:
            print("\nLLM candidates:")
            print(json.dumps(extra.get("llm_candidates"), indent=2, ensure_ascii=False))
            print("\nLLM choice:")
            print(json.dumps(extra.get("llm_choice"), indent=2, ensure_ascii=False))

        if args.debug:
            print("\nPreview:")
            print(json.dumps(
                preview_extraction(tree, result, base_url=extra.get("base_url")),
                indent=2,
                ensure_ascii=False,
            ))

    else:
        result = extract_forum_structure(
            url=args.url,
            file_path=args.file,
            use_llm=not args.no_llm,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
