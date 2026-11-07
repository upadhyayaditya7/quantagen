import os
import json
from datetime import datetime, timezone

import fitz
import tiktoken
import re
from rules.parser import (
    strip_recurring_noise, truncate_at_stop_markers, extract_sections, load_config
)
from rules import layout
def get_token_count(text: str, model: str = "gpt-4o") -> int:
    encoder = tiktoken.encoding_for_model(model)
    return len(encoder.encode(text))

def filter_noise(text: str, losses=None) -> str:
    r"""Universal structural filter: Removes noise based on layout density.

    Unicode-aware: letters and digits from any script count as content, so
    non-Latin text (Japanese, Cyrillic, Arabic, ...) is preserved. Only lines
    that are genuinely symbol-dense are removed. Underscore runs count as
    symbols (they are layout, not content).

    If `losses` is a list, every removed line is appended to it as a ledger
    entry (stage, reason, removed preview, char count).
    """
    clean_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped: continue
            
        # 1. Structural check: Lines that are short and contain numbers.
        #    The length cap matters: after layout reflow a line is a whole
        #    paragraph, and "cost of the project 2500" must not be mistaken
        #    for a page number.
        if len(stripped) <= 40 and re.match(r'^(\d+|[a-zA-Z\s]+\s?\d+/?\d*)$', stripped, re.IGNORECASE):
            if losses is not None:
                losses.append({
                    "stage": "line filter",
                    "reason": "page-number-like line",
                    "removed": stripped[:160],
                    "chars": len(stripped),
                })
            continue
            
        # 2. Density check: Removes lines that are mostly symbols (non-textual noise).
        #    [^\W_] = Unicode letters/digits; whitespace also counts as content.
        content_chars = len(re.findall(r"[^\W_]|\s", stripped))
        if len(stripped) > 0 and ((len(stripped) - content_chars) / len(stripped)) > 0.5:
            if losses is not None:
                losses.append({
                    "stage": "line filter",
                    "reason": "symbol-dense line (>50% non-letter characters)",
                    "removed": stripped[:160],
                    "chars": len(stripped),
                })
            continue
            
        clean_lines.append(stripped)
    return "\n".join(clean_lines)

def trim_structural_edges(text: str, losses=None) -> str:
    """Blunt front/back-matter trim driven by config.json structural_rules.

    Removes the top n% and bottom n% of the raw text, where cover pages,
    copyright blocks, tables of contents, and colophons typically live.
    No-op when both percentages are 0 or absent. Removed spans go to
    `losses` when provided.
    """
    rules = load_config().get("structural_rules", {})
    top = float(rules.get("strip_top_n_percent", 0) or 0)
    bottom = float(rules.get("strip_bottom_n_percent", 0) or 0)
    if top <= 0 and bottom <= 0:
        return text
    n = len(text)
    head = text[:int(n * top)] if top > 0 else ""
    start = int(n * top)
    end = n - int(n * bottom) if bottom > 0 else n
    tail = text[end:] if bottom > 0 else ""
    if start >= end:
        return text
    if losses is not None:
        for part, label in ((head, "head trim"), (tail, "tail trim")):
            if part:
                losses.append({
                    "stage": "structural trim",
                    "reason": label,
                    "removed": part[:160],
                    "chars": len(part),
                })
    return text[start:end]

def clean_pipeline(raw_text: str, stage_tokens: dict | None = None,
                   losses: list | None = None) -> str:
    """The single canonical cleaning pipeline, shared by CLI and UI.

    Order: structural edge trim -> line-level noise filter -> stop-marker
    truncation -> config-driven recurring-noise patterns.

    If `stage_tokens` is a dict, it is filled with the tokens removed at each
    stage, so the audit can show where the savings actually come from.
    If `losses` is a list, every removed span is appended to it as a loss-
    ledger entry: {stage, reason, removed (<=160-char preview), chars, tokens}. 
    """
    stage_fns = (
        ("structural trim", trim_structural_edges),
        ("line filter", filter_noise),
        ("stop-marker truncation", truncate_at_stop_markers),
        ("recurring-noise patterns", strip_recurring_noise),
    )
    text = raw_text
    prev_tokens = get_token_count(text)
    for name, fn in stage_fns:
        text = fn(text, losses) if losses is not None else fn(text)
        if stage_tokens is not None:
            tokens = get_token_count(text)
            stage_tokens[name] = max(0, prev_tokens - tokens)
            prev_tokens = tokens
    if losses is not None:
        for entry in losses:
            if "tokens" not in entry:
                entry["tokens"] = get_token_count(entry["removed"])
    return text

def sieve_text(raw_text):
    """Backwards-compatible alias for clean_pipeline()."""
    return clean_pipeline(raw_text)

def process_document(file_path: str):
    category = os.path.basename(os.path.dirname(file_path))
    file_name = os.path.basename(file_path)
    output_folder = "outputs"
    os.makedirs(output_folder, exist_ok=True)
    
    try:
        # 1. Extract raw text (naive per-page stream: the honest raw baseline)
        doc = fitz.open(file_path)
        raw_text = "\n".join([page.get_text() for page in doc])
        raw_token_count = get_token_count(raw_text)

        # 2. Layout extraction: reading order, margin furniture, paragraph reflow
        stage_tokens = {}
        losses = []
        extracted = layout.extract_text(doc, losses)
        stage_tokens["layout extraction"] = max(0, raw_token_count - get_token_count(extracted))

        # 3. Process (single canonical pipeline, same as the UI)
        step2 = clean_pipeline(extracted, stage_tokens, losses)
        
        # 4. Extract Metadata
        sections = extract_sections(step2)
        clean_token_count = get_token_count(step2)
        # 5. Accurate Impact Analysis
        saved_tokens = max(0, raw_token_count - clean_token_count)
        reduction_pct = (saved_tokens / raw_token_count * 100) if raw_token_count > 0 else 0
        
        # 5b. Loss ledger entry for the machine-readable audit trail
        document_report = {
            "document": file_name,
            "category": category,
            "raw_tokens": raw_token_count,
            "cleaned_tokens": clean_token_count,
            "reduction_pct": round(reduction_pct, 4),
            "stage_tokens": stage_tokens,
            "sections": sections,
            "losses": losses,
        }
        DOCUMENT_REPORTS.append(document_report)
        
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
        for name, n in stage_tokens.items():
            if n:
                print(f"  - {name:<26}{n:>7} tokens")
        if sections:
            print("Sections:        " + ", ".join(f"{k} ({len(v)} chars)" for k, v in sections.items()))
        else:
            print("Sections:        none detected")
        if losses:
            print(f"Loss ledger:     {len(losses)} spans removed (full trail in report.json)")
        
        return step2, sections
        
    except Exception as e:
        import traceback
        print(f"Failed to process {file_path}: {e}")
        traceback.print_exc()
        return None, None

# Per-document audit records, accumulated across a batch run.
DOCUMENT_REPORTS = []


def write_report(path="outputs/report.json"):
    """Persist the accumulated loss ledger / audit trail as report.json."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "engine_version": "0.1.0",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "documents": DOCUMENT_REPORTS,
        }, f, indent=2, ensure_ascii=False)
    return path


def extract_text_from_stream(file_stream):
    doc = fitz.open(stream=file_stream.read(), filetype="pdf")
    return layout.extract_text(doc)

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
        if DOCUMENT_REPORTS:
            report_path = write_report()
            print(f"\nAudit trail written to {report_path} "
                  f"({len(DOCUMENT_REPORTS)} document(s)).")
        print("\nBatch processing complete.")