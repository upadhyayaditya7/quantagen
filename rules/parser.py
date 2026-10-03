import re
import json
import os

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config.json')

def load_config():
    with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)

def strip_recurring_noise(text):
    config = load_config()
    patterns = config.get("global_noise_patterns", [])
    for pattern in patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE | re.MULTILINE)
    return text

def truncate_at_stop_markers(text):
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
        return text[:match.start()]
    return text

def extract_sections(text):
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
            
    # Extract Conclusion
    for marker in markers.get("conclusion", []):
        pattern = rf"{marker}\.?\s*(.*)"
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            sections['conclusion'] = match.group(1).strip()
            break
            
    return sections