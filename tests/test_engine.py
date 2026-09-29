"""Behavioral checks for simulation, consent boundaries and CLI output."""

import json
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from respondent_lab.builder import build_panel, read_source_records
from respondent_lab.cli import main
from respondent_lab.cost import estimate_cost
from respondent_lab.demo import demo_questionnaire, generate_demo_panel
from respondent_lab.engine import run_survey
from respondent_lab.models import Persona, Question, Questionnaire, SYNTHETIC_NOTICE
from respondent_lab.providers import LocalProvider, OpenAIResponsesProvider
from respondent_lab.report import markdown_report
from respondent_lab.sampling import stratified_sample


class EngineTests(unittest.TestCase):
    def test_demo_panel_is_thousand_unique_fictional_profiles(self):
        panel = generate_demo_panel()
        self.assertEqual(len(panel), 1000)
        self.assertEqual({p.segment for p in panel}, {"student", "working_adult"})
        self.assertEqual(sum(p.segment == "student" for p in panel), 500)
        self.assertEqual(len({p.id for p in panel}), 1000)
        self.assertTrue(all(p.synthetic and p.consent_scope == "synthetic_demo" for p in panel))
        self.assertEqual(panel, generate_demo_panel())
        self.assertGreaterEqual(len({p.major for p in panel if p.segment == "student"}), 8)
        self.assertTrue(all(p.industry != "未提供" for p in panel if p.segment == "working_adult"))

    def test_stratified_sampling_is_exact_and_repeatable(self):
        panel = generate_demo_panel()
        sample = stratified_sample(panel, 101, seed=7, proportions={"student": 0.7, "working_adult": 0.3})
        self.assertEqual(len(sample), 101)
        self.assertEqual(sum(p.segment == "student" for p in sample), 71)
        self.assertEqual([p.id for p in sample], [p.id for p in stratified_sample(panel, 101, seed=7,
                         proportions={"student": 0.7, "working_adult": 0.3})])

    def test_survey_answers_and_summary_are_reproducible(self):
        sample = stratified_sample(generate_demo_panel(), 24)
        first = run_survey(sample, demo_questionnaire(), seed=99, review_fraction=0.1)
        second = run_survey(sample, demo_questionnaire(), seed=99, review_fraction=0.1)
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.summary["sample_size"], 24)
        self.assertEqual(sum(first.summary["questions"]["q1"]["counts"].values()), 24)
        self.assertEqual(first.metadata["reviewed_personas"], 3)
        self.assertTrue(all({"major", "industry", "occupation"}.issubset(r) for r in first.responses))
        self.assertTrue(all("[模拟回答]" in r["answers"]["q4"] for r in first.responses))
        self.assertIn("合成模拟报告", markdown_report(first))
        self.assertEqual(first.to_dict()["notice"], SYNTHETIC_NOTICE)

    def test_questionnaire_validation(self):
        with self.assertRaises(ValueError):
            Questionnaire("duplicate", (Question("q", "a", "single_choice", ("x", "y")),
                                        Question("q", "b", "single_choice", ("x", "y"))))
        with self.assertRaises(ValueError):
            Question("bad", "bad", "single_choice", ("only",))

    def test_consented_derived_profile_requires_scope_and_api_permission(self):
        data = generate_demo_panel(per_segment=1)[0].to_dict()
        data.update(synthetic=False, provenance="study-2026-consented", consent_scope="survey_simulation")
        persona = Persona.from_dict(data)
        result = run_survey([persona], demo_questionnaire())
        self.assertEqual(result.metadata["panel_sources"], {"consented_profile": 1})
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}):
            with self.assertRaisesRegex(ValueError, "external_model"):
                run_survey([persona], demo_questionnaire(), provider=OpenAIResponsesProvider())
        data["consent_scope"] = "synthetic_demo"
        with self.assertRaises(ValueError):
            Persona.from_dict(data)

    def test_no_key_falls_back_without_network(self):
        with patch.dict(os.environ, {}, clear=True):
            provider = OpenAIResponsesProvider()
            panel = generate_demo_panel(per_segment=1)
            result = run_survey(panel, demo_questionnaire(), provider=provider)
            self.assertFalse(provider.available)
            self.assertEqual(result.metadata["provider_fallback_count"], 2)

    def test_custom_base_url_chat_completions_mode(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["payload"] = json.loads(request.data)
            return io.BytesIO(b'{"choices":[{"message":{"content":"ok"}}]}')

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key", "OPENAI_BASE_URL": "https://example.test/v1",
                                  "OPENAI_API_MODE": "chat_completions"}):
            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                provider = OpenAIResponsesProvider()
                self.assertEqual(provider._response("gpt-6-sol", "instruction", "prompt"), "ok")
        self.assertEqual(captured["url"], "https://example.test/v1/chat/completions")
        self.assertEqual(captured["payload"]["messages"][1]["content"], "prompt")

    def test_cost_is_explicit_scenario(self):
        estimate = estimate_cost(1000, 1, review_fraction=0.02)
        self.assertEqual((estimate.generation_calls, estimate.review_calls), (1000, 20))
        self.assertIsNone(estimate.estimated_cost_usd)
        priced = estimate_cost(1000, 1, review_fraction=0.02,
                               sol_input_usd_per_million=1, sol_output_usd_per_million=2,
                               astra_input_usd_per_million=5, astra_output_usd_per_million=10)
        self.assertGreater(priced.estimated_cost_usd, 0)

    def test_cli_writes_machine_and_human_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "result.json"
            report = Path(directory) / "report.md"
            code = main(["demo", "--sample-size", "8", "--output", str(output), "--report", str(report)])
            self.assertEqual(code, 0)
            data = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(data["summary"]["sample_size"], 8)
            self.assertIn("模拟", report.read_text(encoding="utf-8"))

    def test_build_panel_requires_consent_and_uses_sol_plus_astra(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.jsonl"
            source.write_text(json.dumps({
                "source_id": "anonymous-001", "provenance": "study-consent-001",
                "consent_scope": "survey_simulation, external_model",
                "profile_text": "22岁大学生，喜欢音乐和学习工具，位于华东。",
                "deidentified": True,
            }, ensure_ascii=False) + "\n", encoding="utf-8")
            records = read_source_records(source)
            self.assertEqual(len(records), 1)
            class MockProvider(OpenAIResponsesProvider):
                def __post_init__(self):
                    self.calls = []
                    super().__post_init__()

                def _response(self, model, instructions, prompt, **kwargs):
                    self.calls.append((model, prompt))
                    if model == "gpt-6-sol":
                        return json.dumps({"segment": "student", "age": 22, "region": "华东",
                                           "education": "本科", "occupation": "学生",
                                           "interests": ["音乐", "学习工具"], "traits": {}})
                    return json.dumps({"status": "pass", "note": "资料支持提炼字段。"})

            with patch.dict(os.environ, {"OPENAI_API_KEY": "mock-key"}):
                provider = MockProvider()
                panel, audit = build_panel(records, provider, review_fraction=0.02)
            self.assertEqual([call[0] for call in provider.calls], ["gpt-6-sol", "gpt-6-astra"])
            self.assertEqual(audit["reviewed_profiles"], 1)
            self.assertFalse(panel[0].synthetic)
            self.assertNotIn("anonymous-001", json.dumps(panel[0].to_dict()))
            self.assertNotIn("profile_text", json.dumps(panel[0].to_dict()))

    def test_build_panel_dry_run_never_uses_api(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.json"
            output = Path(directory) / "panel.json"
            source.write_text(json.dumps([{
                "source_id": "anonymous-002", "provenance": "consent-record",
                "consent_scope": "survey_simulation,external_model",
                "profile_text": "受访者在职，位于华南。", "deidentified": True,
            }], ensure_ascii=False), encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(main(["build-panel", "--provider", "openai", "--input", str(source),
                                       "--output", str(output), "--dry-run"]), 0)
                self.assertEqual(main(["build-panel", "--provider", "openai", "--input", str(source),
                                       "--output", str(output)]), 2)
            self.assertFalse(output.exists())

    def test_checkpoint_recovers_paid_progress_and_allows_unknown_age(self):
        records = []
        for index in range(3):
            records.append({
                "source_id": f"anonymous-{index}", "provenance": "consent-record",
                "consent_scope": "survey_simulation,external_model",
                "profile_text": f"去标识化记录 {index}：学生，专业计算机；年龄未提供。",
                "deidentified": True,
            })
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.json"
            checkpoint = Path(directory) / "progress.json"
            source.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
            selected = read_source_records(source)

            class FailingProvider(OpenAIResponsesProvider):
                def __post_init__(self):
                    self.calls = []
                    super().__post_init__()

                def _response(self, model, instructions, prompt, **kwargs):
                    self.calls.append((model, kwargs))
                    if len(self.calls) == 2:
                        raise OSError("temporary API outage")
                    return json.dumps({"segment": "student", "age": None, "region": "未提供",
                                       "education": "未提供", "occupation": "学生", "major": "计算机",
                                       "industry": "未提供", "interests": [], "traits": {}}, ensure_ascii=False)

            class WorkingProvider(FailingProvider):
                def _response(self, model, instructions, prompt, **kwargs):
                    self.calls.append((model, kwargs))
                    return json.dumps({"segment": "student", "age": None, "region": "未提供",
                                       "education": "未提供", "occupation": "学生", "major": "计算机",
                                       "industry": "未提供", "interests": [], "traits": {}}, ensure_ascii=False)

            with patch.dict(os.environ, {"OPENAI_API_KEY": "mock-key"}):
                first = FailingProvider()
                with self.assertRaisesRegex(RuntimeError, "completed profiles: 1"):
                    build_panel(selected, first, review_fraction=0, checkpoint_path=checkpoint)
                progress = json.loads(checkpoint.read_text(encoding="utf-8"))
                self.assertEqual(len(progress["personas"]), 1)
                first_id = progress["personas"][0]["id"]
                self.assertEqual(len(progress["salt"]), 64)
                self.assertEqual(progress["last_error"]["message"], "temporary API outage")
                self.assertNotIn("profile_text", checkpoint.read_text(encoding="utf-8"))
                second = WorkingProvider()
                panel, audit = build_panel(selected, second, review_fraction=0, checkpoint_path=checkpoint)
            self.assertEqual(len(panel), 3)
            self.assertEqual(panel[0].id, first_id)
            self.assertNotIn(progress["salt"], json.dumps([p.to_dict() for p in panel]))
            self.assertNotIn(progress["salt"], json.dumps(audit))
            self.assertIsNone(panel[0].age)
            self.assertEqual(audit["resumed_profiles"], 1)
            self.assertEqual(len(second.calls), 2)
            self.assertTrue(all(call[1] == {"max_output_tokens": 800, "reasoning_effort": "low"}
                                for call in second.calls))

    def test_major_filter_selects_only_matching_students(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "filtered.json"
            self.assertEqual(main(["demo", "--major", "计算机", "--output", str(output)]), 0)
            data = json.loads(output.read_text(encoding="utf-8"))
            self.assertGreater(data["summary"]["sample_size"], 0)
            self.assertEqual(data["summary"]["segments"], {"student": data["summary"]["sample_size"]})
            self.assertEqual(data["metadata"]["filters"]["major"], "计算机")


if __name__ == "__main__":
    unittest.main()
