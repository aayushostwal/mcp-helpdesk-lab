"""A small BM25 index over the API operations (no dependencies).

Each operation becomes one document. The id, summary and tag are repeated so that a match there
counts more than a match somewhere in the long description. Words are lower-cased, split on
anything that is not a letter or digit (so ``tickets_close`` becomes ``tickets`` and ``close``),
stripped of a few common endings and filtered through a short stop list.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from helpdesk_mcp.spec import Operation

STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "for",
        "from",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "up",
        "use",
        "want",
        "with",
        "what",
        "which",
        "you",
        "your",
    ]
)
K1 = 1.5
B = 0.75
# How many times each field is repeated in the document text.
FIELD_WEIGHTS = {"operation_id": 3, "summary": 2, "tag": 1, "description": 1, "params": 1}

_WORD = re.compile(r"[a-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def stem(word: str) -> str:
    """Light stemming so ``tickets``/``ticket`` and ``closing``/``closed``/``close`` match."""
    if len(word) <= 3 or word.isdigit():
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("sses"):
        return word[:-2]
    for suffix in ("ing", "ed"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)]
            if len(word) > 3 and word[-1] == word[-2] and word[-1] not in "sz":
                word = word[:-1]  # cancelled -> cancell -> cancel, shipping -> ship
            break
    else:
        if word.endswith("es") and word[-3] in "sxz":
            word = word[:-2]
        elif word.endswith("s") and not word.endswith(("ss", "us")):
            word = word[:-1]
    if len(word) > 3 and word.endswith("e"):
        word = word[:-1]  # close -> clos, merge -> merg
    return word


def tokenize(text: str) -> list[str]:
    words = _WORD.findall(_CAMEL.sub(" ", text).lower())
    return [stem(w) for w in words if w not in STOPWORDS]


@dataclass(frozen=True)
class Hit:
    operation_id: str
    score: float


class Bm25Index:
    """Okapi BM25 over pre-tokenised documents."""

    def __init__(self, documents: Sequence[tuple[str, str]]) -> None:
        self.ids = [doc_id for doc_id, _ in documents]
        self.term_freqs = [Counter(tokenize(text)) for _, text in documents]
        self.lengths = [sum(tf.values()) for tf in self.term_freqs]
        self.avg_length = sum(self.lengths) / max(len(self.lengths), 1)
        doc_freq: Counter[str] = Counter()
        for tf in self.term_freqs:
            doc_freq.update(tf.keys())
        n = len(self.ids)
        self.idf = {
            term: math.log(1 + (n - df + 0.5) / (df + 0.5)) for term, df in doc_freq.items()
        }

    def search(self, query: str, limit: int = 5) -> list[Hit]:
        terms = list(dict.fromkeys(tokenize(query)))
        hits: list[Hit] = []
        for doc_id, tf, length in zip(self.ids, self.term_freqs, self.lengths, strict=True):
            score = 0.0
            for term in terms:
                freq = tf.get(term, 0)
                if freq:
                    norm = freq + K1 * (1 - B + B * length / self.avg_length)
                    score += self.idf[term] * freq * (K1 + 1) / norm
            if score > 0:
                hits.append(Hit(doc_id, score))
        hits.sort(key=lambda h: (-h.score, h.operation_id))
        return hits[: max(limit, 0)]


def document_text(op: Operation) -> str:
    fields = {
        "operation_id": op.operation_id,
        "summary": op.summary,
        "tag": op.tag,
        "description": op.description,
        "params": " ".join([*op.argument_names, op.path]),
    }
    return " ".join(" ".join([text] * FIELD_WEIGHTS[name]) for name, text in fields.items())


def build_index(operations: Iterable[Operation]) -> Bm25Index:
    return Bm25Index([(op.operation_id, document_text(op)) for op in operations])
