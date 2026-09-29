"""Simulation engine and descriptive summaries."""

from __future__ import annotations

import hashlib
import math
import random
from collections import Counter
from collections.abc import Sequence
from typing import Any

from .models import Persona, Question, Questionnaire, SYNTHETIC_NOTICE, SurveyResult
from .providers import AnswerProvider, LocalProvider, OpenAIResponsesProvider, ReviewProvider


def _rng(seed: int, persona_id: str, question_id: str) -> random.Random:
    digest = hashlib.sha256(f"{seed}:{persona_id}:{question_id}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _weights(question: Question, persona: Persona) -> list[float]:
    weights = list(question.weights_by_segment.get(persona.segment, (1.0,) * len(question.options)))
    # Explicit segment weights carry the scenario. Traits add only small,
    # deterministic variation so identical profiles need not answer identically.
    if persona.traits:
        sensitivity = sum(persona.traits.values()) / len(persona.traits)
        weights = [weight * (1 + (sensitivity - 0.5) * (index - (len(weights) - 1) / 2) * 0.1)
                   for index, weight in enumerate(weights)]
    return weights


def _weighted_choice(rng: random.Random, options: Sequence[str], weights: Sequence[float]) -> str:
    return rng.choices(options, weights=weights, k=1)[0]


def _closed_answer(persona: Persona, question: Question, *, seed: int) -> str | list[str]:
    rng = _rng(seed, persona.id, question.id)
    weights = _weights(question, persona)
    if question.type != "multiple_choice":
        return _weighted_choice(rng, question.options, weights)
    count = rng.randint(question.min_selections, question.max_selections)
    remaining = list(zip(question.options, weights))
    selected: list[str] = []
    for _ in range(count):
        option = _weighted_choice(rng, [item[0] for item in remaining], [item[1] for item in remaining])
        selected.append(option)
        remaining = [item for item in remaining if item[0] != option]
    return [option for option in question.options if option in selected]


def summarize(questionnaire: Questionnaire, responses: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Descriptive counts over simulated responses, with no inference claims."""
    segment_counts = Counter(response["segment"] for response in responses)
    questions: dict[str, Any] = {}
    n = len(responses)
    for question in questionnaire.questions:
        if question.type == "open_text":
            questions[question.id] = {
                "type": question.type,
                "response_count": sum(bool(response["answers"].get(question.id)) for response in responses),
            }
            continue
        counts = Counter({option: 0 for option in question.options})
        by_segment: dict[str, Counter[str]] = {segment: Counter({option: 0 for option in question.options}) for segment in segment_counts}
        for response in responses:
            answer = response["answers"].get(question.id)
            values = answer if isinstance(answer, list) else [answer]
            for value in values:
                if value in counts:
                    counts[value] += 1
                    by_segment[response["segment"]][value] += 1
        questions[question.id] = {
            "type": question.type,
            "counts": dict(counts),
            # Multiple-choice percentages use respondent count as denominator.
            "percentages": {option: round(count / n * 100, 1) if n else 0.0 for option, count in counts.items()},
            "by_segment": {
                segment: {
                    "count": segment_counts[segment],
                    "counts": dict(values),
                    "percentages": {option: round(count / segment_counts[segment] * 100, 1)
                                    for option, count in values.items()},
                }
                for segment, values in by_segment.items()
            },
        }
    return {"sample_size": n, "segments": dict(segment_counts), "questions": questions}


def run_survey(
    panel: Sequence[Persona], questionnaire: Questionnaire, *, seed: int = 42,
    provider: AnswerProvider | None = None, review_provider: ReviewProvider | None = None,
    review_fraction: float = 0.0,
) -> SurveyResult:
    """Generate simulated answers for an explicitly supplied panel.

    `review_fraction` selects a deterministic subset of open answers. Review
    notes never modify responses. A fraction of 0 performs no review/API calls.
    """
    if not 0 <= review_fraction <= 1:
        raise ValueError("review_fraction must be between 0 and 1")
    if not panel:
        raise ValueError("panel cannot be empty")
    if len({persona.id for persona in panel}) != len(panel):
        raise ValueError("panel persona IDs must be unique")
    provider = provider or LocalProvider()
    def allows_external(persona: Persona) -> bool:
        return persona.synthetic or "external_model" in {scope.strip() for scope in persona.consent_scope.split(",")}

    if isinstance(provider, OpenAIResponsesProvider) and provider.available:
        if any(not allows_external(p) for p in panel):
            raise ValueError("consented profiles need external_model in consent_scope before API use")
    if isinstance(review_provider, OpenAIResponsesProvider) and review_provider.available and review_fraction:
        if any(not allows_external(p) for p in panel):
            raise ValueError("consented profiles need external_model in consent_scope before API review")
    responses: list[dict[str, Any]] = []
    for persona in panel:
        answers: dict[str, Any] = {}
        for question in questionnaire.questions:
            if question.type == "open_text":
                answers[question.id] = provider.answer(persona, question, seed=seed)
            else:
                answers[question.id] = _closed_answer(persona, question, seed=seed)
        responses.append({
            "persona_id": persona.id, "segment": persona.segment,
            "major": persona.major, "industry": persona.industry,
            "occupation": persona.occupation,
            "answers": answers, "reviews": {},
        })
    open_questions = [question for question in questionnaire.questions if question.type == "open_text"]
    review_count = math.ceil(len(panel) * review_fraction) if review_fraction and open_questions else 0
    if review_count:
        reviewer = review_provider or LocalProvider()
        ranked = sorted(range(len(panel)), key=lambda index: hashlib.sha256(
            f"review:{seed}:{panel[index].id}".encode()).digest())
        for index in ranked[:review_count]:
            persona = panel[index]
            for question in open_questions:
                responses[index]["reviews"][question.id] = reviewer.review(
                    persona, question, responses[index]["answers"][question.id], seed=seed,
                )
    source_counts = Counter("synthetic_demo" if persona.synthetic else "consented_profile" for persona in panel)
    metadata = {
        "seed": seed,
        "response_kind": "simulated",
        "panel_sources": dict(source_counts),
        "answer_provider": provider.name,
        "provider_fallback_count": getattr(provider, "fallback_count", 0),
        "provider_fallback_reason": getattr(provider, "disabled_reason", None),
        "review_provider": (review_provider or LocalProvider()).name if review_count else None,
        "reviewed_personas": review_count,
        "review_fallback_count": getattr(review_provider, "review_fallback_count", 0) if review_count else 0,
        "review_last_error": getattr(review_provider, "last_review_error", None) if review_count else None,
        "assumptions": "Closed-answer weights and generated text are scenario assumptions, not measured probabilities.",
        "notice": SYNTHETIC_NOTICE,
    }
    return SurveyResult(questionnaire, responses, summarize(questionnaire, responses), metadata)
