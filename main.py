import os
import fitz
import tiktoken
import re

# Defined at the top so it is available to the entire script
def get_token_count(text: str, model: str = "gpt-4o") -> int:
    encoder = tiktoken.encoding_for_model(model)
    return len(encoder.encode(text))

def filter_noise(text: str) -> str:
    """Removes common PDF boilerplate that inflates token counts."""
    text = re.sub(r'Page \d+ of \d+|Page \d+', '', text, flags=re.IGNORECASE)
    text = re.sub(r'http[s]?://\S+', '', text)
    text = re.sub(r'\.{3,}', '', text)
    return text.strip()

def sieve_text(text: str) -> str:
    text = filter_noise(text)
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def process_document(file_name: str):
    input_folder = "inputs"
    output_folder = "outputs"
    
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
        
    input_path = os.path.join(input_folder, file_name)
    
    try:
        # 1. Extract raw text
        doc = fitz.open(input_path)
        raw_text = "\n".join([page.get_text() for page in doc])
        raw_token_count = get_token_count(raw_text)
        
        # 2. Process
        step1 = filter_noise(raw_text)
        step2 = sieve_text(step1)
        clean_token_count = get_token_count(step2)
        
        # 3. Analyze impact
        saved_tokens = raw_token_count - clean_token_count
        reduction_pct = (saved_tokens / raw_token_count * 100) if raw_token_count > 0 else 0
        
        # 4. Save
        clean_name = os.path.basename(file_name).replace('.pdf', '.txt')
        output_filename = os.path.join(output_folder, f"cleaned_{clean_name}")
        
        with open(output_filename, "w", encoding="utf-8") as f:
            f.write(step2)
        
        # 5. Output Audit Report
        print(f"\n--- Audit Report: {file_name} ---")
        print(f"Raw Tokens:     {raw_token_count}")
        print(f"Cleaned Tokens: {clean_token_count}")
        print(f"Noise Removed:  {saved_tokens} tokens ({reduction_pct:.2f}% efficiency)")
        print(f"Saved to:       {output_filename}")
        
        return step2
    except Exception as e:
        print(f"Failed to process {file_name}: {e}")
        return None

if __name__ == "__main__":
    input_folder = "inputs"
    
    if not os.path.exists(input_folder):
        print(f"Error: '{input_folder}' folder not found. Please create it and add your PDFs.")
    else:
        files = [f for f in os.listdir(input_folder) if f.lower().endswith(".pdf")]
        
        if not files:
            print("No PDF files found in the 'inputs' folder.")
        else:
            print(f"Quantagen Engine v0.1.0: Found {len(files)} files. Starting batch processing...")
            for file_name in files:
                process_document(file_name)
            print("\nBatch processing complete.")