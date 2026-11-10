"""Packaging guarantees: an installed wheel must be self-contained and honest.

These exist because packaging introduces its own class of drift: a second copy
of the config, a second list of pins, a version string in two places. Each is
checked here instead of being left to a comment asking people to keep them in
sync.
"""

import json
import re
from pathlib import Path

import pytest

import quantagen
from quantagen import cli
from quantagen.rules import parser

ROOT = Path(__file__).resolve().parents[1]


def test_version_is_single_sourced():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # setuptools reads the version from the package, so bumping it in one place
    # is enough; a hard-coded copy in pyproject would silently drift.
    assert 'attr = "quantagen.__version__"' in pyproject
    assert re.fullmatch(r"\d+\.\d+\.\d+", quantagen.__version__)


def test_packaged_default_config_matches_the_checkout():
    # The root config.json is the one contributors edit; the packaged copy is
    # what `pip install quantagen` ships. Users must get the same rules.
    root = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    packaged = json.loads((ROOT / "quantagen" / "config.json").read_text(encoding="utf-8"))
    assert root == packaged


def test_dependency_pins_match_requirements():
    # The retention floors were calibrated against these exact builds, so the
    # wheel must not install a different extractor than the one CI measured.
    pinned = {
        line.strip().lower()
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if "==" in line and not line.strip().startswith("#")
    }
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
    for dependency in ("pymupdf==1.27.2.3", "tiktoken==0.13.0"):
        assert dependency in pinned
        assert dependency in pyproject


def test_config_env_override_wins(monkeypatch, tmp_path):
    custom = tmp_path / "mine.json"
    custom.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("QUANTAGEN_CONFIG", str(custom))
    assert parser.config_path() == str(custom)


def test_console_script_reports_its_version(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--version"])
    assert excinfo.value.code == 0
    assert quantagen.__version__ in capsys.readouterr().out


def test_collect_walks_directories_and_skips_duplicates(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "one.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "two.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "notes.txt").write_text("not a pdf", encoding="utf-8")

    found = cli.collect([str(tmp_path), str(tmp_path / "two.pdf")])

    assert sorted(Path(path).name for path in found) == ["one.pdf", "two.pdf"]
