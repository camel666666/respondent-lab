"""Validated, JSON-friendly survey schemas."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


SYNTHETIC_NOTICE = (
    "SYNTHETIC SIMULATION — generated personas and answers are not real survey "
    "responses, representative estimates, or evidence about any population."
)
QUESTION_TYPES = {"single_choice", "multiple_choice", "likert", "open_text"}


@dataclass(frozen=True)
class Persona:
    id: str
    segment: str
    age: int | None
    region: str
    education: str
    occupation: str
    major: str = "未提供"
    industry: str = "未提供"
    interests: tuple[str, ...] = ()
    traits: dict[str, float] = field(default_factory=dict)
    synthetic: bool = True
    provenance: str = "demo_generator"
    consent_scope: str = "synthetic_demo"

    def __post_init__(self) -> None:
        if not self.id or not self.segment or not self.region:
            raise ValueError("persona id, segment and region are required")
        if self.age is not None and not 16 <= self.age <= 100:
            raise ValueError("persona age must be between 16 and 100")
        scopes = {scope.strip() for scope in self.consent_scope.split(",")}
        if not self.synthetic and (not self.provenance.strip() or "survey_simulation" not in scopes):
            raise ValueError("derived profiles require provenance and survey_simulation consent_scope")
        if any(not 0 <= value <= 1 for value in self.traits.values()):
            raise ValueError("persona traits must be between 0 and 1")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Persona":
        allowed = {"id", "segment", "age", "region", "education", "occupation", "major", "industry", "interests", "traits", "synthetic", "provenance", "consent_scope"}
        if not isinstance(data, dict) or set(data) - allowed:
            raise ValueError("invalid persona fields")
        return cls(
            id=str(data["id"]), segment=str(data["segment"]), age=int(data["age"]) if data["age"] is not None else None,
            region=str(data["region"]), education=str(data["education"]),
            occupation=str(data["occupation"]),
            major=str(data.get("major", "未提供")), industry=str(data.get("industry", "未提供")),
            interests=tuple(data.get("interests", ())),
            traits={str(k): float(v) for k, v in data.get("traits", {}).items()},
            synthetic=bool(data.get("synthetic", True)),
            provenance=str(data.get("provenance", "" if not data.get("synthetic", True) else "demo_generator")),
            consent_scope=str(data.get("consent_scope", "" if not data.get("synthetic", True) else "synthetic_demo")),
        )


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    type: str
    options: tuple[str, ...] = ()
    # Multipliers, not observed probabilities. These are scenario assumptions.
    weights_by_segment: dict[str, tuple[float, ...]] = field(default_factory=dict)
    min_selections: int = 1
    max_selections: int = 2

    def __post_init__(self) -> None:
        if not self.id or not self.text.strip():
            raise ValueError("question id and text are required")
        if self.type not in QUESTION_TYPES:
            raise ValueError(f"unsupported question type: {self.type}")
        if self.type == "open_text" and self.options:
            raise ValueError("open_text questions cannot have options")
        if self.type != "open_text" and len(self.options) < 2:
            raise ValueError("closed questions need at least two options")
        if len(set(self.options)) != len(self.options):
            raise ValueError("question options must be unique")
        if self.type == "likert" and len(self.options) < 3:
            raise ValueError("likert questions need at least three ordered options")
        if self.type == "multiple_choice" and not (0 <= self.min_selections <= self.max_selections <= len(self.options)):
            raise ValueError("invalid multiple-choice selection bounds")
        for weights in self.weights_by_segment.values():
            if len(weights) != len(self.options) or any(weight < 0 for weight in weights) or not any(weights):
                raise ValueError("segment weights must match options and contain a positive value")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Question":
        allowed = {"id", "text", "type", "options", "weights_by_segment", "min_selections", "max_selections"}
        if not isinstance(data, dict) or set(data) - allowed:
            raise ValueError("invalid question fields")
        return cls(
            id=str(data["id"]), text=str(data["text"]), type=str(data["type"]),
            options=tuple(str(x) for x in data.get("options", ())),
            weights_by_segment={str(k): tuple(float(x) for x in v) for k, v in data.get("weights_by_segment", {}).items()},
            min_selections=int(data.get("min_selections", 1)),
            max_selections=int(data.get("max_selections", 2)),
        )


@dataclass(frozen=True)
class Questionnaire:
    title: str
    questions: tuple[Question, ...]
    description: str = ""

    def __post_init__(self) -> None:
        if not self.title.strip() or not self.questions:
            raise ValueError("questionnaire title and at least one question are required")
        ids = [question.id for question in self.questions]
        if len(set(ids)) != len(ids):
            raise ValueError("question IDs must be unique")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Questionnaire":
        allowed = {"title", "description", "questions"}
        if not isinstance(data, dict) or set(data) - allowed:
            raise ValueError("invalid questionnaire fields")
        return cls(
            title=str(data["title"]), description=str(data.get("description", "")),
            questions=tuple(Question.from_dict(q) for q in data["questions"]),
        )


@dataclass
class SurveyResult:
    questionnaire: Questionnaire
    responses: list[dict[str, Any]]
    summary: dict[str, Any]
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "notice": SYNTHETIC_NOTICE,
            "questionnaire": self.questionnaire.to_dict(),
            "responses": self.responses,
            "summary": self.summary,
            "metadata": self.metadata,
        }
