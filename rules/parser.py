import re
import json
import os

def load_config():
    # This will look for config.json in your main directory
    with open('config.json', 'r') as f:
        return json.load(f)

def strip_recurring_noise(text):
    config = load_config()
    patterns = config.get("noise_patterns", [])
    for pattern in patterns:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    return text

def truncate_references(text):
    match = re.search(r"\nREFERENCES\s*", text, re.IGNORECASE)
    if match:
        return text[:match.start()]
    return text