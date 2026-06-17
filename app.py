import streamlit as st
import pandas as pd
from main import (
    filter_noise, sieve_text, get_token_count, 
    extract_text_from_stream, calc_efficiency
)

st.set_page_config(page_title="Quantagen Batch Engine", layout="wide")
st.title("Quantagen Batch Processing Engine")

uploaded_files = st.file_uploader("Upload research PDFs", type="pdf", accept_multiple_files=True)

if uploaded_files:
    results = []
    
    # Create tabs for individual file previews
    tabs = st.tabs([file.name for file in uploaded_files])
    
    for i, uploaded_file in enumerate(uploaded_files):
        with tabs[i]:
            # Process
            raw_text = extract_text_from_stream(uploaded_file)
            clean_text = sieve_text(raw_text)
            
            # Metrics
            raw_tokens = get_token_count(raw_text)
            efficiency = calc_efficiency(raw_text, clean_text)
            
            # Display
            col1, col2 = st.columns(2)
            col1.metric("Raw Tokens", f"{raw_tokens:,}")
            col2.metric("Efficiency Gain", f"{efficiency:.2f}%")
            
            st.text_area(f"Preview: {uploaded_file.name}", clean_text, height=500)
            
            st.download_button(
                label=f"Download {uploaded_file.name.replace('.pdf', '.txt')}",
                data=clean_text,
                file_name=f"cleaned_{uploaded_file.name.replace('.pdf', '.txt')}",
                mime="text/plain"
            )
            
            results.append({"File": uploaded_file.name, "Raw": raw_tokens, "Efficiency": f"{efficiency:.2f}%"})

    # Show a summary table
    st.divider()
    st.subheader("Batch Summary Audit")
    st.table(pd.DataFrame(results))