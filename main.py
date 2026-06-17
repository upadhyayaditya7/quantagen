import fitz
import tiktoken
import re
import sys

def filter_noise(text: str) -> str:
    """Removes common PDF boilerplate that inflates token counts."""
    # Remove patterns like "Page 1 of 10" or "Page 1"
    text = re.sub(r'Page \d+ of \d+|Page \d+', '', text, flags=re.IGNORECASE)
    
    # Remove URLs (often just noise in doc-to-text)
    text = re.sub(r'http[s]?://\S+', '', text)
    
    # Remove excessive punctuation sequences (e.g., "....")
    text = re.sub(r'\.{3,}', '', text)
    
    return text.strip()

def sieve_text(text: str) -> str:
    # Combine original whitespace cleaning with the new noise filter
    text = filter_noise(text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def get_token_count(text: str, model: str = "gpt-4o") -> int:
    """Helper for audit metrics."""
    encoder = tiktoken.encoding_for_model(model)
    return len(encoder.encode(text))

def process_document(file_path: str):
    doc = fitz.open(file_path)
    raw_text = "\n".join([page.get_text() for page in doc])
    
    # Track stages
    step1 = filter_noise(raw_text)
    step2 = sieve_text(step1) # whitespace cleanup
    
    orig_tokens = get_token_count(raw_text)
    final_tokens = get_token_count(step2)
    
    # Audit summary
    print(f"--- Quantagen Audit Report ---")
    print(f"Initial: {orig_tokens} tokens")
    print(f"Final:   {final_tokens} tokens")
    print(f"Overall Efficiency Gain: {((orig_tokens - final_tokens) / orig_tokens) * 100:.2f}%")
    
    return step2

if __name__ == "__main__":
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
        print(f"Quantagen Engine v0.1.0 Processing: {file_path}")
        process_document(file_path)
    else:
        print("Usage: python main.py <path_to_pdf>")