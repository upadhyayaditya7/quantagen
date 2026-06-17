import streamlit as st
from main import (
    filter_noise, sieve_text, get_token_count, 
    extract_text_from_stream, calc_efficiency
)

st.title("Quantagen Engine v0.1.0")
st.subheader("High-Performance PDF Token Optimizer")

uploaded_file = st.file_uploader("Upload a research PDF", type="pdf")

if uploaded_file is not None:
    # Processing
    raw_text = extract_text_from_stream(uploaded_file)
    clean_text = sieve_text(raw_text)
    
    # Audit Metrics
    raw_tokens = get_token_count(raw_text)
    efficiency = calc_efficiency(raw_text, clean_text)
    
    col1, col2 = st.columns(2)
    col1.metric("Raw Tokens", f"{raw_tokens:,}")
    col2.metric("Efficiency Gain", f"{efficiency:.2f}%")
    
    # Output Area
    st.text_area("Cleaned Content (Preview)", clean_text, height=300)
    
    # Download
    st.download_button(
        label="Download Cleaned Text",
        data=clean_text,
        file_name=f"cleaned_{uploaded_file.name.replace('.pdf', '.txt')}",
        mime="text/plain"
    )