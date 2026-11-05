from main import filter_noise


def test_standalone_page_number_lines_are_dropped():
    assert filter_noise("8\nNormal body line.") == "Normal body line."
    assert "Page 3" not in filter_noise("Page 3\nBody.")


def test_prose_lines_are_kept():
    prose = "The committee drafts reports quarterly."
    assert filter_noise(prose) == prose


def test_symbol_dense_lines_are_dropped():
    out = filter_noise("### =====---\nReal sentence.")
    assert "Real sentence." in out
    assert "###" not in out


def test_underscore_runs_are_dropped():
    assert filter_noise("______\nText.") == "Text."


def test_non_latin_text_survives():
    # Regression: the density filter once counted only [a-zA-Z\d\s] as content,
    # so Japanese/Cyrillic/Arabic lines scored ~100% symbols and were deleted.
    text = "実験装置は低温で動作する。\nCyrillic line пример."
    out = filter_noise(text)
    assert "実験装置は低温で動作する。" in out
    assert "Cyrillic line пример." in out


def test_non_latin_kept_but_layout_noise_still_dropped():
    out = filter_noise("実験装置は低温で動作する。\n______\n8\nNormal line.")
    assert "実験装置" in out
    assert "______" not in out
    assert "\n8" not in out


def test_blank_lines_are_flattened():
    # Documented behavior: paragraph structure is flattened (known limitation).
    assert filter_noise("a\n\n\nb") == "a\nb"


def test_lines_are_exception_preserving():
    # Non-ASCII whitespace / odd spacing lines must not crash the filter.
    assert filter_noise("\t\n\x0cText.") == "Text."
