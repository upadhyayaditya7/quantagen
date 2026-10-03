import os
import fitz
import tiktoken
import re
from rules.parser import strip_recurring_noise, truncate_at_stop_markers, extract_sections 

def get_token_count(text: str, model: str = "gpt-4o") -> int:
    encoder = tiktoken.encoding_for_model(model)
    return len(encoder.encode(text))

def filter_noise(text: str) -> str:
    r"""Universal structural filter: Removes noise based on layout density.

    Unicode-aware: letters and digits from any script count as content, so
    non-Latin text (Japanese, Cyrillic, Arabic, ...) is preserved. Only lines
    that are genuinely symbol-dense are removed. Underscore runs count as
    symbols (they are layout, not content).
    """
    clean_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped: continue
            
        # 1. Structural check: Lines that are short and contain numbers
        if re.match(r'^(\d+|[a-zA-Z\s]+\s?\d+/?\d*)$', stripped, re.IGNORECASE):
            continue
            
        # 2. Density check: Removes lines that are mostly symbols (non-textual noise).
        #    [^\W_] = Unicode letters/digits; whitespace also counts as content.
        content_chars = len(re.findall(r"[^\W_]|\s", stripped))
        if len(stripped) > 0 and ((len(stripped) - content_chars) / len(stripped)) > 0.5:
            continue
            
        clean_lines.append(stripped)
    return "\n".join(clean_lines)

def sieve_text(raw_text):
    clean_text = truncate_at_stop_markers(raw_text)
    clean_text = strip_recurring_noise(clean_text)
    return clean_text

def process_document(file_path: str):
    category = os.path.basename(os.path.dirname(file_path))
    file_name = os.path.basename(file_path)
    output_folder = "outputs"
    os.makedirs(output_folder, exist_ok=True)
    
    try:
        # 1. Extract raw text
        doc = fitz.open(file_path)
        raw_text = "\n".join([page.get_text() for page in doc])
        raw_token_count = get_token_count(raw_text)
        
        # 2. Process
        step2 = sieve_text(filter_noise(raw_text))
        
        # 3. Debugging (Character level check)
        removed_chars = len(raw_text) - len(step2)
        print(f"DEBUG: Removed {removed_chars} characters from the document.")
        
        # 4. Extract Metadata
        sections = extract_sections(step2)
        clean_token_count = get_token_count(step2)
        
        # 5. Accurate Impact Analysis
        saved_tokens = max(0, raw_token_count - clean_token_count)
        reduction_pct = (saved_tokens / raw_token_count * 100) if raw_token_count > 0 else 0
        
        # 6. Save final result
        clean_name = file_name.replace('.pdf', '.txt')
        output_filename = os.path.join(output_folder, f"cleaned_{category}_{clean_name}")
        
        with open(output_filename, "w", encoding="utf-8") as f:
            f.write(step2)
        
        # 7. Audit Report
        print(f"\n--- Audit Report: {file_name} ---")
        print(f"Raw Tokens:      {raw_token_count}")
        print(f"Cleaned Tokens:  {clean_token_count}")
        print(f"Efficiency:      {reduction_pct:.4f}% ({saved_tokens} tokens removed)")
        
        return step2, sections
        
    except Exception as e:
        print(f"Failed to process {file_path}: {e}")
        return None, None

def extract_text_from_stream(file_stream):
    doc = fitz.open(stream=file_stream.read(), filetype="pdf")
    return "\n".join([page.get_text() for page in doc])

def calc_efficiency(raw_text, clean_text):
    raw = get_token_count(raw_text)
    clean = get_token_count(clean_text)
    saved = raw - clean
    return (saved / raw * 100) if raw > 0 else 0

if __name__ == "__main__":
    input_root = "inputs"
    
    if not os.path.exists(input_root):
        print(f"Error: '{input_root}' folder not found.")
    else:
        print(f"Quantagen Engine v0.1.0: Starting recursive scan of {input_root}...")
        # Recursive traversal of all subdirectories
        for root, dirs, files in os.walk(input_root):
            for file in files:
                if file.lower().endswith(".pdf"):
                    full_path = os.path.join(root, file)
                    process_document(full_path)
        print("\nBatch processing complete.")