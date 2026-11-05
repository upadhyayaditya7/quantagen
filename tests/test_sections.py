from rules.parser import extract_sections


def test_abstract_extracted_until_numbered_section():
    text = "ABSTRACT\nThis study examines tuning effects in depth.\n1. INTRODUCTION\nBody follows."
    s = extract_sections(text)
    assert s["abstract"] == "This study examines tuning effects in depth."


def test_conclusion_bounded_by_next_numbered_section():
    text = "SUMMARY\nChapter wraps up the method here.\n2. NEXT SECTION\nmore text continues"
    s = extract_sections(text)
    assert "Chapter wraps up the method here." in s["conclusion"]
    assert "more text continues" not in s["conclusion"]


def test_book_per_chapter_summary_does_not_swallow_rest():
    # Regression: a per-chapter "Summary" heading in a book once captured
    # everything to the end of the document as the "conclusion".
    text = (
        "SUMMARY\nFirst chapter wraps up.\n"
        "THE NEXT CHAPTER BEGINS\nLots of later content follows here."
    )
    s = extract_sections(text)
    assert "First chapter wraps up." in s["conclusion"]
    assert "Lots of later content follows here." not in s["conclusion"]


def test_conclusion_extracts_from_real_heading():
    text = "Body.\nCONCLUSION\nWe showed tuning wins conclusively.\nFinal note."
    s = extract_sections(text)
    assert "We showed tuning wins conclusively." in s["conclusion"]


def test_no_sections_returns_empty():
    assert extract_sections("nothing resembling a marker here") == {}
