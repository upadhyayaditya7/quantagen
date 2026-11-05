from rules.parser import strip_recurring_noise


def test_stamp_lines_removed():
    text = "DRAFT version 2\nKeep me.\n© 2024 Acme Corp"
    out = strip_recurring_noise(text)
    assert "DRAFT version" not in out
    assert "© 2024" not in out
    assert "Keep me." in out


def test_prose_using_draft_survives():
    out = strip_recurring_noise("The committee drafts reports weekly.")
    assert "drafts reports weekly." in out


def test_page_n_of_m_removed():
    text = "Real paragraph text.\nPage 12 of 30\nSecond paragraph."
    out = strip_recurring_noise(text)
    assert "Page 12 of 30" not in out
    assert "Real paragraph text." in out
    assert "Second paragraph." in out


def test_issn_line_removed():
    # The configured ISSN pattern anchors on <digits>.ISSN; a line whose
    # ISSN key precedes its number is a different (unsupported) shape.
    text = "Vol 12 2024 ISSN 1234-5678 header\nBody text remains."
    assert "ISSN 1234-5678 header" not in strip_recurring_noise(text)
    assert "Body text remains." in strip_recurring_noise(text)


def test_patterns_apply_anywhere_not_only_string_start():
    # re.MULTILINE regression: ^...$ patterns once matched only line #1.
    text = "Line one.\nDRAFT version 8\nLine three.\n© 2020 Publisher"
    out = strip_recurring_noise(text)
    assert "Line one." in out
    assert "Line three." in out
    assert "DRAFT version 8" not in out
    assert "© 2020 Publisher" not in out


def test_underscore_runs_removed_here_too():
    assert strip_recurring_noise("Body.\n___________") == "Body.\n"
