"""A client that answers nothing, for checking the wiring before a model is pointed at the corpus.

A run against this should score 0 with no errors and no items in review. If it scores above zero, something in the
question set is answerable without reading the document, which is worth knowing before the real run.
"""

from __future__ import annotations


def ask(question: str, document: str) -> str:
    return "لا تتوفر لدي هذه المعلومة."
