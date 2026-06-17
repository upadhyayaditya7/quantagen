import fitz
import tiktoken
import re

def sieve_text(text: str) -> str:
    # Basic cleanup: Remove excessive whitespace
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def process_pdf(file_path: str):
    # 1. Parse
    doc = fitz.open(file_path)
    raw_text = "\n".join([page.get_text() for page in doc])
    
    # 2. Sieve
    optimized_text = sieve_text(raw_text)
    
    # 3. Audit
    enc = tiktoken.encoding_for_model("gpt-4o")
    orig_tokens = len(enc.encode(raw_text))
    opt_tokens = len(enc.encode(optimized_text))
    
    print(f"Original: {orig_tokens} tokens")
    print(f"Optimized: {opt_tokens} tokens")
    print(f"Saved: {orig_tokens - opt_tokens} tokens")
    
    return optimized_text

if __name__ == "__main__":
    # Replace 'sample.pdf' with a real file path
    # output = process_pdf("sample.pdf")
    print("Quantagen Engine Initialized.")