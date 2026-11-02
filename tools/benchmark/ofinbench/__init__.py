"""A benchmark built from real, de-identified client documents, to find out what a model actually scores on them.

The model's current figures were measured on data produced by the same generator it was trained on, which makes them
a measure of fit to that generator rather than of ability on a Saudi bank statement. These tools exist to replace
that number with one worth quoting: de-identify a document, record what the right answers are, generate the
questions, and score any model through a callable the caller supplies.

Nothing here touches the product at runtime. It is an offline measurement kit, and it refuses to keep its corpus
anywhere a commit could reach.
"""

__all__ = ["anonymize", "detect", "items", "numbers", "paths", "pseudonym", "score", "truth"]
