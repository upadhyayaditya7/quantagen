"""Uniform extractor interface for the benchmark.

An adapter answers three questions and nothing else: can it run on this machine,
what third-party build is doing the work, and what text does it produce for one
PDF. Competing tools are imported lazily and never at module import time, so a
machine without Docling still lists the adapter and reports it as unavailable
instead of failing to start the benchmark.
"""

from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass


class MissingTool(RuntimeError):
    """The extractor is not installed here; the message says how to install it."""


class ExtractionUnavailable(RuntimeError):
    """The extractor is installed but cannot read this document (e.g. image-only)."""


@dataclass(frozen=True)
class Adapter:
    name: str
    module: str
    requires: tuple
    install: str

    def available(self) -> bool:
        return all(importlib.util.find_spec(module) is not None for module in self.requires)

    def version(self) -> str:
        """Best-effort build string, so a score can be tied to a version."""
        for module in self.requires:
            try:
                found = importlib.import_module(module)
            except ImportError:
                continue
            for attribute in ("__version__", "version"):
                value = getattr(found, attribute, None)
                if isinstance(value, str):
                    return f"{module} {value}"
        return "version unknown"

    def extract(self, path: str) -> str:
        if not self.available():
            raise MissingTool(f"{self.name} is not installed; run `{self.install}`")
        return importlib.import_module(self.module).extract(path)


ADAPTERS = {
    adapter.name: adapter
    for adapter in (
        Adapter("quantagen", "bench.adapters.quantagen", ("fitz", "tiktoken"), "pip install -e ."),
        Adapter("raw", "bench.adapters.raw_pymupdf", ("fitz",), "pip install -e ."),
        Adapter("pymupdf4llm", "bench.adapters.pymupdf4llm", ("pymupdf4llm",), "pip install pymupdf4llm"),
        Adapter("markitdown", "bench.adapters.markitdown", ("markitdown",), "pip install markitdown"),
        Adapter("docling", "bench.adapters.docling", ("docling",), "pip install docling"),
    )
}


def get(name: str) -> Adapter:
    if name not in ADAPTERS:
        raise KeyError(f"unknown adapter {name!r}; known: {', '.join(sorted(ADAPTERS))}")
    return ADAPTERS[name]


def names() -> list:
    return sorted(ADAPTERS)


def availability() -> dict:
    """Which extractors this machine can actually run."""
    return {name: adapter.available() for name, adapter in sorted(ADAPTERS.items())}
