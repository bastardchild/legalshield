"""
Skill-store fingerprinting tests (KNOWN_ISSUES #17).

The previous key was `clause_type:severity`, so every non-compete clause ever seen
collapsed into a single row and the first example was the only one retained.
"""
from app.services.skill_store import CONFIDENCE_THRESHOLD, fingerprint


class TestFingerprint:
    def test_stable_across_calls(self):
        a = fingerprint("non_compete", "Dilarang bekerja untuk kompetitor.")
        b = fingerprint("non_compete", "Dilarang bekerja untuk kompetitor.")
        assert a == b

    def test_length_is_32(self):
        assert len(fingerprint("x", "y")) == 32

    def test_whitespace_and_case_insensitive(self):
        a = fingerprint("non_compete", "Dilarang  bekerja\nuntuk kompetitor.")
        b = fingerprint("non_compete", "dilarang bekerja untuk kompetitor.")
        assert a == b

    def test_leading_trailing_whitespace_ignored(self):
        assert fingerprint("t", "  teks  ") == fingerprint("t", "teks")

    def test_different_examples_differ(self):
        a = fingerprint("non_compete", "Dilarang bekerja untuk kompetitor selama 5 tahun.")
        b = fingerprint("non_compete", "Dilarang bekerja untuk kompetitor selama 1 tahun.")
        assert a != b

    def test_different_clause_types_differ(self):
        text = "Teks klausul yang sama persis."
        assert fingerprint("non_compete", text) != fingerprint("payment_terms", text)

    def test_empty_example_is_handled(self):
        assert len(fingerprint("other", "")) == 32
        assert fingerprint("other", "") == fingerprint("other", None)


def test_confidence_threshold_is_in_unit_range():
    """A threshold outside [0,1] would either store everything or nothing."""
    assert 0.0 < CONFIDENCE_THRESHOLD <= 1.0
