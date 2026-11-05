from main import clean_pipeline


def _ending(s: str) -> str:
    """Result of a pipeline run that should have truncated to this text."""
    return s


def test_standalone_references_heading_truncates():
    paper = "Body text.\nREFERENCES\n[1] Some citation that should vanish."
    assert clean_pipeline(paper) == _ending("Body text.\n")


def test_appendix_with_section_letter_truncates():
    assert "after" not in clean_pipeline("Keep.\nAppendix A\nafter text")


def test_index_with_colon_truncates():
    assert "code" not in clean_pipeline("Keep.\nINDEX:\ncode text")


def test_lowercase_genuine_heading_truncates():
    assert "bibliography text" not in clean_pipeline("Keep.\nbibliography\nbibliography text")


def test_prose_containing_marker_words_never_truncates():
    prose = (
        "Intro paragraph.\n"
        "The index of refraction changes with temperature.\n"
        "References in this paper discuss prior work.\n"
        "More prose."
    )
    out = clean_pipeline(prose)
    assert "More prose." in out
    assert "refraction" in out


def test_in_sentence_references_line_survives():
    out = clean_pipeline("Keep this.\nReferences in this paper discuss X fully.")
    assert "discuss X fully." in out


def test_bibliography_as_inline_word_is_safe():
    out = clean_pipeline("The bibliography section mentions works.")
    assert "mentions works." in out
