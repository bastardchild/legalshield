"""
Tests for LLM response handling.

`extract_json_object` is the fix for KNOWN_ISSUES #7: strict `json.loads` on the raw
response failed a whole agent whenever the model wrapped its output in a fence or added
a sentence of prose.
"""
import pytest

from app.services.llm_client import (
    LLMJSONError,
    _is_response_format_error,
    _normalize_base_url,
    _outermost_object,
    extract_json_object,
)


class TestNormalizeBaseUrl:
    """KNOWN_ISSUES #10 — the README's Ollama tip produced `.../v1/v1` and 404s."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("http://host.docker.internal:11434", "http://host.docker.internal:11434"),
            ("http://host.docker.internal:11434/v1", "http://host.docker.internal:11434"),
            ("http://host.docker.internal:11434/v1/", "http://host.docker.internal:11434"),
            ("  https://api.example.com/  ", "https://api.example.com"),
            ("https://api.example.com/v1///", "https://api.example.com"),
        ],
    )
    def test_strips_trailing_version_segment(self, raw, expected):
        assert _normalize_base_url(raw) == expected

    def test_does_not_strip_v1_inside_path(self):
        assert _normalize_base_url("https://api.example.com/v1/proxy") == (
            "https://api.example.com/v1/proxy"
        )


class TestExtractJsonObject:
    def test_plain_object(self):
        assert extract_json_object('{"findings": []}') == {"findings": []}

    def test_json_fence(self):
        raw = '```json\n{"findings": [{"severity": "high"}]}\n```'
        assert extract_json_object(raw)["findings"][0]["severity"] == "high"

    def test_unlabelled_fence(self):
        assert extract_json_object('```\n{"a": 1}\n```') == {"a": 1}

    def test_preamble(self):
        assert extract_json_object('Berikut hasil analisisnya:\n{"a": 1}') == {"a": 1}

    def test_trailing_commentary(self):
        raw = '{"a": 1}\n\nSemoga membantu! Beri tahu saya jika ada pertanyaan.'
        assert extract_json_object(raw) == {"a": 1}

    def test_preamble_and_trailing_commentary(self):
        raw = 'Sure, here you go:\n{"a": {"b": 2}}\nLet me know if you need more.'
        assert extract_json_object(raw) == {"a": {"b": 2}}

    def test_nested_objects_preserved(self):
        raw = '{"findings": [{"meta": {"deep": {"deeper": 1}}}]} trailing'
        assert extract_json_object(raw)["findings"][0]["meta"]["deep"]["deeper"] == 1

    def test_braces_inside_strings(self):
        raw = '{"explanation": "Pasal ini memuat {kurung} yang aneh"} done'
        assert extract_json_object(raw)["explanation"] == "Pasal ini memuat {kurung} yang aneh"

    def test_escaped_quote_inside_string(self):
        raw = '{"explanation": "klausul \\"force majeure\\" tidak jelas"}'
        assert '"force majeure"' in extract_json_object(raw)["explanation"]

    def test_empty_response_raises(self):
        with pytest.raises(LLMJSONError):
            extract_json_object("   ")

    def test_no_json_raises(self):
        with pytest.raises(LLMJSONError):
            extract_json_object("Maaf, saya tidak bisa membantu.")

    def test_truncated_object_raises(self):
        """finish_reason=length leaves an unbalanced object; must not parse as partial."""
        with pytest.raises(LLMJSONError):
            extract_json_object('{"findings": [{"clause_type": "non_comp')

    def test_json_array_raises(self):
        """Agents index into keys, so a bare array is not usable."""
        with pytest.raises(LLMJSONError):
            extract_json_object('[{"a": 1}]')


class TestOutermostObject:
    def test_returns_none_without_brace(self):
        assert _outermost_object("no object here") is None

    def test_returns_none_when_unbalanced(self):
        assert _outermost_object('{"a": {"b": 1}') is None

    def test_stops_at_first_balanced_object(self):
        assert _outermost_object('{"a": 1} {"b": 2}') == '{"a": 1}'


class TestResponseFormatErrorDetection:
    @pytest.mark.parametrize(
        "message",
        [
            "Unrecognized request argument supplied: response_format",
            "json_object is not supported by this model",
            "400 Bad Request: response_format not supported",
        ],
    )
    def test_detects_provider_rejection(self, message):
        assert _is_response_format_error(Exception(message))

    @pytest.mark.parametrize(
        "message",
        ["Connection error", "429 rate limit exceeded", "model not found"],
    )
    def test_ignores_unrelated_errors(self, message):
        assert not _is_response_format_error(Exception(message))
