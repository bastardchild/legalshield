"""
Upload contract filter: is this document actually a contract / legal paper?

Every accepted upload costs three LLM agent calls, so junk must be rejected cheaply,
before any queueing happens. The filter scores the extracted text against a weighted
legal lexicon (`seed/legal_lexicon.json`, ~5100 positive entries + ~600 negative
entries) and a small set of structural markers that only appear in real legal
documents, then compares the result to a configurable threshold.

Design decisions worth stating:

* **Fail open, never block on infrastructure.** If the lexicon file is missing or
  unreadable the assessment accepts the document and logs a warning. A misconfigured
  deployment should cost LLM budget, not strand a user's upload — the filter is a
  gate, not a hard dependency of the product.
* **Lexicon density, not raw hits.** `score_lex` is `1 - exp(-k * contribution/tokens)`:
  a saturation curve on the per-token weighted-hit rate. It is deliberately *not* a
  plain ratio or raw count — long contracts would always win and short ones would be
  unfairly rejected, while a naive ratio would classify a CV with two legal words as
  legal. The cap at 3 occurrences per term stops a single repeated word ("PASAL PASAL
  PASAL...") from gaming the score.
* **Negatives depress, they never veto.** A document full of invoice vocabulary still
  passes if it carries real contract structure; the penalty is bounded (`<= 0.4`) so a
  single strong negative phrase can't sink a genuine contract. Words that are
  genuinely both (e.g. "faktur" appears in contracts *and* invoices) live on the
  positive side of the lexicon, so the negative score only sees vocabulary with no
  legal reading.
* **Structure markers are patterns, not words.** Numbered articles ("Pasal 5"),
  numbered clause lines, "antara ... dengan", "dengan ini", date blocks, "nomor 123"
  — these signal a legal document's layout, which no lexicon can capture.
"""

import json
import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)

LEXICON_FILENAME = "legal_lexicon.json"

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_PASAL_RE = re.compile(r"\bpasal\s+(?:[0-9]{1,4}|[ivxlcdm]+)\b")
_AYAT_RE = re.compile(r"\bayat\s+[0-9]{1,3}\b")
_NOMOR_RE = re.compile(r"\bnomor\s+[0-9]+")
_DATE_RE = re.compile(r"\b[0-9]{1,2}[-/][0-9]{1,2}[-/][0-9]{2,4}\b")
_NUMBERED_LINE_RE = re.compile(r"^\s*[0-9]{1,3}[.)]\s", re.MULTILINE)


def _word(pattern: str) -> re.Pattern:
    """Compile a word-boundary regex: 'antara' must not match inside 'Nusantara'."""
    return re.compile(r"\b" + pattern + r"\b")


# Structure markers: (compiled pattern, weight). Weights are chosen so that a real
# contract accumulates ~10-20 points, a CV/recipe/invoice accumulates near zero. Every
# marker is a word-boundary regex — a substring match would let "Nusantara" count as
# "antara" and "menominal" count as "nomor".
_STRUCTURE_MARKERS: list[tuple[re.Pattern, int]] = [
    (_PASAL_RE, 5),
    (_word(r"dengan\s+ini"), 3),
    (_AYAT_RE, 3),
    (_word("antara"), 2),
    (_word(r"tanda\s+tangan"), 2),
    (_word(r"berlaku\s+sejak"), 2),
    (_word("nomor"), 2),
    (_word("ditandatangani"), 3),
    (_word("sepakat"), 2),
    (_word("materai"), 2),
]

_LEX_K = 12.0        # sensitivity of the lexicon saturation curve
_NEG_K = 2.0         # sensitivity of the negative penalty: density, not presence
_STRUCT_K = 0.05     # sensitivity of the structure saturation curve
_MAX_PENALTY = 0.40  # negatives can never fully veto structure + lexicon


@dataclass(frozen=True)
class Lexicon:
    """The loaded lexicon: positive single terms, positive phrases, negative n-grams."""

    terms: dict[str, int]
    phrases: dict[str, int]
    negative: dict[str, int]
    max_phrase_len: int = 0
    max_negative_len: int = 0


@dataclass
class ContractAssessment:
    is_contract: bool
    score: float
    reason: str = ""
    token_count: int = 0
    lexicon_hits: int = 0
    phrase_hits: int = 0
    negative_hits: int = 0
    structure_weight: int = 0
    structure_hits: list[str] = field(default_factory=list)


@lru_cache(maxsize=1)
def load_lexicon(path: str | None = None) -> Lexicon | None:
    """Load and cache the lexicon JSON. Returns None on any failure (caller fails open)."""
    target = Path(path or (Path(get_settings().seed_dir) / LEXICON_FILENAME))
    try:
        with target.open(encoding="utf-8") as fh:
            data = json.load(fh)
        terms = data.get("terms") or {}
        phrases = data.get("phrases") or {}
        negative = data.get("negative_terms") or {}
        return Lexicon(
            terms={k: int(v) for k, v in terms.items()},
            phrases={k: int(v) for k, v in phrases.items()},
            negative={k: int(v) for k, v in negative.items()},
            max_phrase_len=max((len(p.split()) for p in phrases), default=0),
            max_negative_len=max((len(p.split()) for p in negative), default=0),
        )
    except Exception as e:  # noqa: BLE001 - any loading error must fail open, not 500
        logger.error(f"[ContractFilter] failed to load lexicon {target}: {e!r}")
        return None


def normalize_text(text: str) -> str:
    """Lowercase and collapse whitespace; keeps word order for phrase matching."""
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def tokenize(text: str) -> list[str]:
    """Lowercase, then split into alphanumeric runs; punctuation and hyphens separate.

    Lowercasing happens here (not just in `normalize_text`) so a caller that passes raw
    document text cannot silently produce wrong tokens: "Pasal" must yield "pasal",
    not "asal". Idempotent for already-lowercased input.
    """
    return _TOKEN_RE.findall((text or "").lower())


def _ngram_counts(tokens: list[str], max_len: int) -> list[Counter]:
    """Counts of every n-gram of length 1..max_len, as a list indexed by length-1."""
    n = len(tokens)
    counts: list[Counter] = []
    for length in range(1, max_len + 1):
        counter: Counter = Counter()
        for i in range(n - length + 1):
            counter[" ".join(tokens[i : i + length])] += 1
        counts.append(counter)
    return counts


def _contribution(counts: list[Counter], lexicon: dict[str, int],
                  max_len: int) -> tuple[float, int]:
    """Weighted hit contribution, capped at 3 occurrences per distinct n-gram."""
    total = 0.0
    distinct = 0
    for length in range(1, max_len + 1):
        for ngram, count in counts[length - 1].items():
            weight = lexicon.get(ngram)
            if weight is not None:
                total += min(3, count) * weight
                distinct += 1
    return total, distinct


def _structure_weight(normalized: str) -> tuple[int, list[str]]:
    total = 0
    hits: list[str] = []
    for pattern, weight in _STRUCTURE_MARKERS:
        if pattern.search(normalized):
            total += weight
            hits.append(pattern.pattern)
    # Numbered clause lines ("1. ...", "2) ...") are a strong layout signal.
    numbered = len(_NUMBERED_LINE_RE.findall(normalized))
    if numbered >= 3:
        total += 3
        hits.append("numbered-clauses")
    # "antara X dengan Y" — the parties of an agreement. Both must appear as whole words.
    if _word("antara").search(normalized) and _word("dengan").search(normalized):
        total += 3
        hits.append("antara-dengan")
    if _DATE_RE.search(normalized):
        total += 2
        hits.append("date-block")
    if _NOMOR_RE.search(normalized):
        total += 2
        hits.append("nomor-number")
    return total, hits


def assess_contract(
    text: str,
    lexicon: Lexicon | None = None,
    min_score: float | None = None,
    min_tokens: int | None = None,
) -> ContractAssessment:
    """
    Score a document against the legal lexicon and structural markers.

    `lexicon` is injectable for tests; when None the real one is loaded (and when that
    fails, the document is accepted — fail open). `min_score`/`min_tokens` override the
    settings, again for tests.
    """
    settings = get_settings()
    if min_score is None:
        min_score = settings.contract_filter_min_score
    if min_tokens is None:
        min_tokens = settings.contract_filter_min_tokens

    normalized = normalize_text(text)
    tokens = tokenize(normalized)
    token_count = len(tokens)

    if token_count == 0:
        return ContractAssessment(False, 0.0, reason="empty")
    if token_count < min_tokens:
        return ContractAssessment(False, 0.0, reason="too_short", token_count=token_count)

    if lexicon is None:
        lexicon = load_lexicon()
    if lexicon is None:
        logger.warning("[ContractFilter] lexicon unavailable; accepting document (fail open).")
        return ContractAssessment(True, 1.0, reason="lexicon_unavailable", token_count=token_count)

    # One n-gram array sized to the longest positive *or* negative phrase; negative
    # phrases are often longer than positive ones.
    max_len = max(lexicon.max_phrase_len, lexicon.max_negative_len, 1)
    counts = _ngram_counts(tokens, max_len)

    # A single-token term in both `terms` and `phrases` (the lexicon contains a few
    # single-word phrases) contributes once — terms win the weight.
    positives = {**lexicon.phrases, **lexicon.terms}
    pos_contrib, pos_hits = _contribution(counts, positives, max(lexicon.max_phrase_len, 1))
    neg_contrib, neg_hits = _contribution(
        counts, lexicon.negative, lexicon.max_negative_len
    )

    lex_contrib = pos_contrib
    score_lex = 1 - math.exp(-_LEX_K * lex_contrib / token_count)

    struct_weight, struct_hits = _structure_weight(normalized)
    score_struct = 1 - math.exp(-_STRUCT_K * struct_weight)

    penalty = min(_MAX_PENALTY, _NEG_K * neg_contrib / token_count)

    score = 0.6 * score_lex + 0.4 * score_struct - penalty
    score = max(0.0, min(1.0, score))

    return ContractAssessment(
        is_contract=score >= min_score,
        score=score,
        reason="accepted" if score >= min_score else "low_score",
        token_count=token_count,
        lexicon_hits=pos_hits,
        phrase_hits=pos_hits,
        negative_hits=neg_hits,
        structure_weight=struct_weight,
        structure_hits=struct_hits,
    )
