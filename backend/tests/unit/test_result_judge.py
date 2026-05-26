"""Testovi za LLM-as-Judge JSON parser.

Judge je opcioni Cascade v3 okidač. Parser mora robusno hvatati JSON iz
LLM output-a koji često ima markdown wrapping ili preamble.
"""

from __future__ import annotations

from app.services.result_judge import _parse_judge_response


class TestJsonParsing:
    def test_clean_json_ok(self) -> None:
        verdict, reason = _parse_judge_response('{"verdict": "OK", "reason": "looks fine"}')
        assert verdict == "OK"
        assert reason == "looks fine"

    def test_clean_json_wrong(self) -> None:
        verdict, reason = _parse_judge_response('{"verdict": "WRONG", "reason": "shape mismatch"}')
        assert verdict == "WRONG"
        assert "shape mismatch" in reason


class TestMarkdownWrapping:
    def test_json_in_sql_code_block(self) -> None:
        raw = '```json\n{"verdict": "WRONG", "reason": "0 rows"}\n```'
        verdict, _ = _parse_judge_response(raw)
        assert verdict == "WRONG"

    def test_json_in_generic_code_block(self) -> None:
        raw = '```\n{"verdict": "OK", "reason": "fine"}\n```'
        verdict, _ = _parse_judge_response(raw)
        assert verdict == "OK"


class TestFallback:
    def test_no_json_defaults_to_ok(self) -> None:
        """Do-no-harm: kad parser ne nađe JSON, vraća OK da ne trigeriramo retry."""

        verdict, reason = _parse_judge_response("This is not JSON")
        assert verdict == "OK"

    def test_invalid_verdict_defaults_to_ok(self) -> None:
        verdict, _ = _parse_judge_response('{"verdict": "MAYBE", "reason": "..."}')
        assert verdict == "OK"

    def test_keyword_fallback_wrong(self) -> None:
        """Ako nema JSON-a ali tekst sadrži "WRONG" bez "OK", parser ga uhvati."""

        verdict, _ = _parse_judge_response("WRONG - this is clearly bad")
        assert verdict == "WRONG"

    def test_empty_string(self) -> None:
        verdict, _ = _parse_judge_response("")
        assert verdict == "OK"
