"""Command-line entry points for reproducible local survey simulations."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .builder import build_panel, read_source_records
from .cost import estimate_cost
from .demo import demo_questionnaire, generate_demo_panel
from .engine import run_survey
from .models import Persona, Questionnaire
from .providers import LocalProvider, OpenAIResponsesProvider
from .report import markdown_report
from .sampling import stratified_sample


def _read_json(path: Path):
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def _panel(path: Path | None, seed: int) -> list[Persona]:
    if path is None:
        return generate_demo_panel(seed=seed)
    data = _read_json(path)
    records = data.get("personas") if isinstance(data, dict) else data
    if not isinstance(records, list):
        raise ValueError("panel JSON must be a list or contain a personas list")
    return [Persona.from_dict(record) for record in records]


def _write_result(result, path: Path, report_path: Path | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if report_path:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(markdown_report(result), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="respondent-lab",
        description="Simulate a survey. Outputs are synthetic and never empirical findings.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("demo", "run"):
        sub = commands.add_parser(name, help="run the bundled demo" if name == "demo" else "run a JSON questionnaire")
        sub.add_argument("--output", type=Path, default=Path("survey-result.json"), help="output JSON path")
        sub.add_argument("--report", type=Path, help="optional Markdown report path")
        sub.add_argument("--seed", type=int, default=42)
        sub.add_argument("--sample-size", type=int, help="defaults to all matching panel profiles")
        sub.add_argument("--major", help="filter to an exact student major, e.g. 计算机")
        sub.add_argument("--industry", help="filter to an exact industry, e.g. 互联网")
        sub.add_argument("--occupation", help="filter to an exact occupation, e.g. 技术岗位")
        sub.add_argument("--student-share", type=float, help="requested student share, if panel has student/working_adult segments")
        sub.add_argument("--panel", type=Path, help="JSON list of de-identified synthetic or consented persona profiles")
        sub.add_argument("--provider", choices=("local", "openai"), default="local",
                         help="openai is opt-in and uses only OPENAI_API_KEY from the environment")
        sub.add_argument("--review-fraction", type=float, default=0.0,
                         help="fraction reviewed with gpt-6-astra for opt-in OpenAI runs")
        sub.add_argument("--model", default="gpt-6-sol")
        sub.add_argument("--review-model", default="gpt-6-astra")
        if name == "run":
            sub.add_argument("--questionnaire", type=Path, required=True)
    costs = commands.add_parser("estimate", help="show a token/cost scenario; prices are user supplied")
    costs.add_argument("--respondents", type=int, default=1000)
    costs.add_argument("--open-questions", type=int, default=1)
    costs.add_argument("--review-fraction", type=float, default=0.02)
    costs.add_argument("--sol-input-rate", type=float, help="USD per million input tokens")
    costs.add_argument("--sol-output-rate", type=float, help="USD per million output tokens")
    costs.add_argument("--astra-input-rate", type=float, help="USD per million input tokens")
    costs.add_argument("--astra-output-rate", type=float, help="USD per million output tokens")
    build = commands.add_parser("build-panel", help="extract authorized de-identified profiles with opt-in OpenAI calls")
    build.add_argument("--input", type=Path, required=True, help="JSON or JSONL source records")
    build.add_argument("--output", type=Path, default=Path("panel.json"), help="derived panel JSON path")
    build.add_argument("--audit", type=Path, default=Path("panel-audit.json"), help="review audit JSON path")
    build.add_argument("--checkpoint", type=Path,
                       help="resume file; defaults to OUTPUT.checkpoint.json and is updated after each API result")
    build.add_argument("--limit", type=int, help="maximum source records to process")
    build.add_argument("--dry-run", action="store_true", help="validate source and show planned calls, without API calls")
    build.add_argument("--provider", choices=("openai",), required=True, help="explicit paid provider opt-in")
    build.add_argument("--review-fraction", type=float, default=0.02)
    build.add_argument("--model", default="gpt-6-sol")
    build.add_argument("--review-model", default="gpt-6-astra")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "estimate":
            estimate = estimate_cost(
                args.respondents, args.open_questions, review_fraction=args.review_fraction,
                sol_input_usd_per_million=args.sol_input_rate,
                sol_output_usd_per_million=args.sol_output_rate,
                astra_input_usd_per_million=args.astra_input_rate,
                astra_output_usd_per_million=args.astra_output_rate,
            )
            print(json.dumps(estimate.to_dict(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "build-panel":
            records = read_source_records(args.input, limit=args.limit)
            if not 0 <= args.review_fraction <= 1:
                raise ValueError("review_fraction must be between 0 and 1")
            planned_reviews = round(len(records) * args.review_fraction)
            if args.review_fraction and not planned_reviews:
                planned_reviews = 1
            if args.dry_run:
                print(json.dumps({
                    "mode": "dry_run", "records": len(records), "sol_calls": len(records),
                    "astra_review_calls": planned_reviews, "api_calls_made": 0,
                    "notice": "No profiles were built and no source data was uploaded.",
                }, ensure_ascii=False, indent=2))
                return 0
            provider = OpenAIResponsesProvider(model=args.model, review_model=args.review_model)
            checkpoint_path = args.checkpoint or Path(str(args.output) + ".checkpoint.json")
            personas, audit = build_panel(records, provider, review_fraction=args.review_fraction,
                                          checkpoint_path=checkpoint_path)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.audit.parent.mkdir(parents=True, exist_ok=True)
            for path, data in ((args.output, {"notice": "Derived from authorized de-identified profiles; survey answers remain simulated.",
                                                      "personas": [persona.to_dict() for persona in personas]}),
                               (args.audit, audit)):
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(data, stream, ensure_ascii=False, indent=2)
                    stream.write("\n")
                path.chmod(0o600)
            print(f"Built {len(personas)} authorized derived profiles; audited {audit['reviewed_profiles']}.")
            print(f"Saved panel to {args.output} and audit to {args.audit}")
            return 0
        panel = _panel(args.panel, args.seed)
        if args.major:
            panel = [persona for persona in panel if persona.major == args.major]
        if args.industry:
            panel = [persona for persona in panel if persona.industry == args.industry]
        if args.occupation:
            panel = [persona for persona in panel if persona.occupation == args.occupation]
        if not panel:
            raise ValueError("no panel profiles match the requested filters")
        sample_size = args.sample_size if args.sample_size is not None else len(panel)
        if sample_size > len(panel):
            raise ValueError(f"sample size {sample_size} exceeds matching panel size {len(panel)}")
        shares = None
        if args.student_share is not None:
            if not 0 <= args.student_share <= 1 or set(p.segment for p in panel) != {"student", "working_adult"}:
                raise ValueError("student share requires a student/working_adult panel and a value between 0 and 1")
            shares = {"student": args.student_share, "working_adult": 1 - args.student_share}
        sample = stratified_sample(panel, sample_size, seed=args.seed, proportions=shares)
        questionnaire = demo_questionnaire() if args.command == "demo" else Questionnaire.from_dict(_read_json(args.questionnaire))
        if args.provider == "openai":
            provider = OpenAIResponsesProvider(model=args.model, review_model=args.review_model)
            reviewer = provider
            if not provider.available:
                print("OPENAI_API_KEY is unset; using local simulated answers and local review.", file=sys.stderr)
                provider = LocalProvider()
                reviewer = LocalProvider()
        else:
            provider = LocalProvider()
            reviewer = LocalProvider()
        result = run_survey(sample, questionnaire, seed=args.seed, provider=provider,
                            review_provider=reviewer, review_fraction=args.review_fraction)
        result.metadata["filters"] = {"major": args.major, "industry": args.industry,
                                      "occupation": args.occupation}
        if result.metadata["provider_fallback_count"] or result.metadata["review_fallback_count"]:
            print(f"Warning: some OpenAI calls fell back or failed; see metadata for details: "
                  f"{result.metadata['provider_fallback_reason'] or result.metadata['review_last_error']}", file=sys.stderr)
        _write_result(result, args.output, args.report)
        print(f"Saved {len(sample)} simulated responses to {args.output}")
        if args.report:
            print(f"Saved synthetic report to {args.report}")
        return 0
    except (ValueError, KeyError, TypeError, OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
