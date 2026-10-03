import re
import json

def load_config():
    with open('config.json', 'r', encoding='utf-8') as f:
        return json.load(f)

def strip_recurring_noise(text):
    config = load_config()
    patterns = config.get("global_noise_patterns", [])
    for pattern in patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE | re.MULTILINE)
    return text

def truncate_at_stop_markers(text):
    config = load_config()
    markers = config.get("stop_markers", [])
    if markers:
        pattern = r"\n(" + "|".join(markers) + r")\s*"
        match = re.search(pattern, text, re.IGNORECASE)
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