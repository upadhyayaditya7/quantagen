import re
import json
import os

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.json')

def load_config():
    with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)

def strip_recurring_noise(text, losses=None):
    config = load_config()
    patterns = config.get("global_noise_patterns", [])
    for pattern in patterns:
        before = text
        text = re.sub(pattern, "", text, flags=re.IGNORECASE | re.MULTILINE)
        if losses is not None and text != before:
            for line in _missing_lines(before, text):
                losses.append({
                    "stage": "recurring-noise patterns",
                    "reason": f"matched configured pattern {pattern}",
                    "removed": line[:160],
                    "chars": len(line),
                })
    return text


def _missing_lines(before, after):
    """Lines present in `before` but absent from `after` (occurrence-counted).

    Called descriptive of removals; line-preserving regexes are the only
    kind this project's config ships, so per-line attribution stays exact.
    """
    from collections import Counter

    surplus = Counter(before.splitlines()) - Counter(after.splitlines())
    return [line for line, n in surplus.items() for _ in range(n)]

def truncate_at_stop_markers(text, losses=None):
    """Truncate the document at the first standalone stop-marker heading.

    The marker must start its own line (case-insensitive) and may only be
    followed by a colon, a section letter ("Appendix A"), or a short number
    before the line ends. Prose like "...for any given\nindex r." or
    "the index of refraction" can therefore never trigger truncation.
    """
    config = load_config()
    markers = config.get("stop_markers", [])
    if not markers:
        return text

    alternatives = "|".join(re.escape(m) for m in markers)
    # Line starts with the marker; only a heading-style tail is allowed:
    # optional spaces, optional colon, optional section letter / short number.
    heading_pattern = rf"^\s*(?:{alternatives})\b\s*:?\s*[A-Za-z]?\d{{0,3}}\s*$"

    match = re.search(heading_pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    if match:
        removed = text[match.start():]
        if losses is not None:
            losses.append({
                "stage": "stop-marker truncation",
                "reason": f"truncated at standalone heading {removed.splitlines()[0]!r}",
                "removed": removed[-160:],
                "chars": len(removed),
            })
        return text[:match.start()]
    return text

def extract_sections(text, losses=None):
    config = load_config()
    sections = {}
    markers = config.get("section_markers", {})
    
    # Extract Abstract
    for marker in markers.get("abstract", []):
        # Looks for the header and grabs everything until the next numbered section or the end
        pattern = rf"{marker}\.?\s*(.*?)(?=\n\d\.|INTRODUCTION|$)"
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            sections['abstract'] = match.group(1).strip()
            break
            
    # Extract Conclusion. Bounded: the capture stops at the next numbered
    # section or a standalone ALL-CAPS heading, so a per-chapter "Summary"
    # heading in a book does not swallow the rest of the document.
    for marker in markers.get("conclusion", []):
        # (?i:...) scopes case-insensitivity to the marker itself; the
        # lookahead stays case-sensitive so only real ALL-CAPS headings bound it.
        pattern = rf"(?i:{marker})\.?\s*(.*?)(?=\n\s*\d|\n\s*[A-Z][A-Z \-]{{3,}}\s*\n|\Z)"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            sections['conclusion'] = match.group(1).strip()
            break
            
    return sections