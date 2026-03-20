# html_parser.py
from bs4 import BeautifulSoup, NavigableString
import re

REMOVE_TAGS = {
    "script", "style", "noscript",
    "svg", "canvas", "iframe",
    "nav", "footer", "header", "aside"
}

BLOCK_TAGS = {
    "p", "div", "section", "article", "main",
    "li", "ul", "ol",
    "table", "tr", "td", "th",
    "form", "label",
    "h1", "h2", "h3", "h4"
}

def extract_readable_page(html: str) -> dict:
    """
    Parses raw HTML and extracts the most relevant readable content and metadata.
    
    This function:
    1. Parses the HTML using BeautifulSoup.
    2. Extracts metadata (title, description, canonical URL) from the <head>.
    3. Removes non-content tags (scripts, styles, nav, footer, etc.).
    4. Identifies the main content area (using <main>, <article>, or <body>).
    5. Extracts text from block-level tags (p, div, h1-h4, etc.) and interactive element hints.
    6. Deduplicates and cleans the extracted text.
    
    Args:
        html: The raw HTML string to parse.
        
    Returns:
        A dictionary containing:
            - head: Metadata dictionary.
            - content: Cleaned, readable text content.
            - word_count: Total words in content.
            - char_count: Total characters in content.
    """
    soup = BeautifulSoup(html, "html.parser")

    # -------------------------
    # HEAD EXTRACTION (Metadata)
    # -------------------------
    def meta(name=None, prop=None):
        """Helper to find meta tag content by name or property."""
        if name:
            tag = soup.find("meta", attrs={"name": name})
        else:
            tag = soup.find("meta", property=prop)
        return tag["content"].strip() if tag and tag.get("content") else None

    head = {
        "title": soup.title.string.strip() if soup.title else None,
        "description": meta(name="description") or meta(prop="og:description"),
        "og_title": meta(prop="og:title"),
        "canonical": (
            soup.find("link", rel="canonical")["href"]
            if soup.find("link", rel="canonical")
            else None
        ),
    }

    # -------------------------
    # BODY CLEANUP (Noise Removal)
    # -------------------------
    # Remove tags that usually don't contain primary content or are for presentation/logic.
    for tag in soup.find_all(REMOVE_TAGS):
        tag.decompose()

    # Look for the most likely main content container.
    main = (
        soup.find("main")
        or soup.find("article")
        or soup.find(attrs={"role": "main"})
        or soup.body
    )

    if not main:
        return {
            "head": head, 
            "content": "",
            "word_count": 0,
            "char_count": 0
        }

    blocks = []

    # -------------------------
    # CONTENT EXTRACTION (Block Analysis)
    # -------------------------
    for el in main.descendants:
        # Extract text from meaningful block tags
        if el.name in BLOCK_TAGS:
            text = " ".join(el.stripped_strings)
            # Only keep blocks with significant text content
            if len(text) > 40:
                blocks.append(text)

        # Extract hints for form elements (useful for agentic interaction)
        if el.name == "input":
            label = None
            if el.get("id"):
                l = soup.find("label", attrs={"for": el["id"]})
                label = l.get_text(strip=True) if l else None

            # Prefer label over placeholder as a functional hint
            hint = label or el.get("placeholder")
            if hint:
                blocks.append(f"[Input] {hint}")

        # Textareas often contain search boxes or comment fields
        if el.name == "textarea" and el.get("placeholder"):
            blocks.append(f"[Textarea] {el['placeholder']}")

    # Deduplicate extracted blocks (preserves order of first occurrence)
    content = "\n\n".join(dict.fromkeys(blocks))
    # Normalize excessive spacing
    content = re.sub(r"\n{3,}", "\n\n", content)

    return {
        "head": head,
        "content": content.strip(),
        "word_count": len(content.split()),
        "char_count": len(content)
    }