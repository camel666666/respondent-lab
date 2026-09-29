"""Explicit budget scenarios; rates are user supplied, never presented as tariffs."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class CostEstimate:
    respondents: int
    open_questions: int
    review_fraction: float
    generation_calls: int
    review_calls: int
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost_usd: float | None
    note: str = "Scenario only. Token counts and user-supplied rates are estimates; check current provider pricing and actual usage."

    def to_dict(self) -> dict:
        return asdict(self)


def estimate_cost(
    respondents: int, open_questions: int, *, review_fraction: float = 0.02,
    input_tokens_per_generation: int = 250, output_tokens_per_generation: int = 80,
    input_tokens_per_review: int = 350, output_tokens_per_review: int = 50,
    sol_input_usd_per_million: float | None = None,
    sol_output_usd_per_million: float | None = None,
    astra_input_usd_per_million: float | None = None,
    astra_output_usd_per_million: float | None = None,
) -> CostEstimate:
    if respondents < 0 or open_questions < 0 or not 0 <= review_fraction <= 1:
        raise ValueError("invalid respondent count, open question count or review fraction")
    token_params = (input_tokens_per_generation, output_tokens_per_generation,
                    input_tokens_per_review, output_tokens_per_review)
    if any(value < 0 for value in token_params):
        raise ValueError("token assumptions cannot be negative")
    rates = (sol_input_usd_per_million, sol_output_usd_per_million,
             astra_input_usd_per_million, astra_output_usd_per_million)
    if any(value is not None and value < 0 for value in rates):
        raise ValueError("rates cannot be negative")
    generation_calls = respondents * open_questions
    review_calls = math.ceil(respondents * review_fraction) * open_questions if open_questions else 0
    input_tokens = generation_calls * input_tokens_per_generation + review_calls * input_tokens_per_review
    output_tokens = generation_calls * output_tokens_per_generation + review_calls * output_tokens_per_review
    cost = None
    if all(rate is not None for rate in rates):
        cost = round((generation_calls * (input_tokens_per_generation * sol_input_usd_per_million +
                                           output_tokens_per_generation * sol_output_usd_per_million) +
                      review_calls * (input_tokens_per_review * astra_input_usd_per_million +
                                      output_tokens_per_review * astra_output_usd_per_million)) / 1_000_000, 4)
    return CostEstimate(respondents, open_questions, review_fraction, generation_calls, review_calls,
                        input_tokens, output_tokens, cost)
