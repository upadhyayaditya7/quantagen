import re
import json

def load_config():
    # Load settings from your external file
    with open('config.json', 'r') as f:
        return json.load(f)

def strip_recurring_noise(text):
    config = load_config()
    # Apply all regex patterns defined in JSON
    patterns = config.get("noise_patterns", [])
    for pattern in patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    return text

def truncate_at_stop_markers(text):
    config = load_config()
    markers = config.get("stop_markers", [])
    
    # Build a regex: \n(REFERENCES|BIBLIOGRAPHY|...)\s*
    # This matches the markers even if they have different formatting
    if markers:
        pattern = r"\n(" + "|".join(markers) + r")\s*"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return text[:match.start()]
    return text