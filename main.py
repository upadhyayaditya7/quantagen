import fitz  # PyMuPDF
import tiktoken
import re

def sieve_text(text: str) -> str:
    """Core cleaning logic."""
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def get_token_count(text: str, model: str = "gpt-4o") -> int:
    """Helper for audit metrics."""
    encoder = tiktoken.encoding_for_model(model)
    return len(encoder.encode(text))

def process_document(file_path: str):
    """Orchestrates parsing and auditing."""
    doc = fitz.open(file_path)
    raw_text = "\n".join([page.get_text() for page in doc])
    
    optimized = sieve_text(raw_text)
    
    print(f"Original tokens: {get_token_count(raw_text)}")
    print(f"Optimized tokens: {get_token_count(optimized)}")
    
    return optimized

if __name__ == "__main__":
    print("Quantagen Engine v0.1.0 Ready.")