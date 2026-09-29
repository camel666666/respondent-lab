"""Open answer generation with an offline default and optional OpenAI provider."""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

from .models import Persona, Question


class AnswerProvider(Protocol):
    name: str

    def answer(self, persona: Persona, question: Question, *, seed: int) -> str: ...


class ReviewProvider(Protocol):
    name: str

    def review(self, persona: Persona, question: Question, answer: str, *, seed: int) -> str: ...


def _stable_index(value: str, length: int) -> int:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % length


@dataclass
class LocalProvider:
    """Deterministic fallback text, explicitly labelled synthetic."""

    name: str = "local_template"

    def answer(self, persona: Persona, question: Question, *, seed: int) -> str:
        interest = persona.interests[0] if persona.interests else "日常使用"
        subjects = ("操作步骤", "提醒设置", "信息整理", "费用说明", "跨设备使用")
        subject = subjects[_stable_index(f"{seed}:{persona.id}:{question.id}", len(subjects))]
        if persona.segment == "student":
            return f"[模拟回答] 希望{subject}更简单，方便我在学习和{interest}之间安排时间。"
        return f"[模拟回答] 希望{subject}更清楚，方便我兼顾工作和{interest}。"

    def review(self, persona: Persona, question: Question, answer: str, *, seed: int) -> str:
        return "本地规则检查：回答已标注为模拟内容；未进行模型事实核验。"


@dataclass
class OpenAIResponsesProvider:
    """Minimal Responses API client; no third-party dependency required.

    If no key is configured, calls use `fallback`. Network/API failures also
    fall back so demos and reports remain usable offline. The optional review
    uses a separate high-end model and is limited by the engine's review count.
    """

    model: str = "gpt-6-sol"
    review_model: str = "gpt-6-astra"
    base_url: str = field(default_factory=lambda: os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"))
    api_mode: str = field(default_factory=lambda: os.environ.get("OPENAI_API_MODE", "responses"))
    timeout: float = 30.0
    fallback: LocalProvider | None = None
    name: str = "openai_responses"
    fallback_count: int = 0
    disabled_reason: str | None = None
    review_fallback_count: int = 0
    last_review_error: str | None = None

    def __post_init__(self) -> None:
        if self.api_mode not in {"responses", "chat_completions"}:
            raise ValueError("OPENAI_API_MODE must be responses or chat_completions")
        if self.fallback is None:
            self.fallback = LocalProvider()

    @property
    def api_key(self) -> str | None:
        return os.environ.get("OPENAI_API_KEY")

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _response(
        self, model: str, instructions: str, prompt: str, *,
        max_output_tokens: int = 400, reasoning_effort: str = "low",
    ) -> str:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured")
        if self.api_mode == "responses":
            path = "/responses"
            parameters = {
                "model": model,
                "instructions": instructions,
                "input": prompt,
                "max_output_tokens": max_output_tokens,
                "reasoning": {"effort": reasoning_effort},
            }
        else:
            path = "/chat/completions"
            parameters = {
                "model": model,
                "messages": [{"role": "system", "content": instructions},
                             {"role": "user", "content": prompt}],
                "max_tokens": max_output_tokens,
            }
        payload = json.dumps(parameters, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url.rstrip('/')}{path}", data=payload,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            data = json.load(response)
        if self.api_mode == "chat_completions":
            choices = data.get("choices") or []
            content = choices[0].get("message", {}).get("content") if choices else None
            if isinstance(content, str) and content.strip():
                return content.strip()
            raise ValueError("Chat Completions API returned no text")
        if isinstance(data.get("output_text"), str) and data["output_text"].strip():
            return data["output_text"].strip()
        chunks = []
        for output in data.get("output", []):
            for content in output.get("content", []):
                if content.get("type") == "output_text" and isinstance(content.get("text"), str):
                    chunks.append(content["text"])
        result = "\n".join(chunks).strip()
        if not result:
            raise ValueError("Responses API returned no text")
        return result

    def answer(self, persona: Persona, question: Question, *, seed: int) -> str:
        if not self.available or self.disabled_reason:
            self.fallback_count += 1
            return self.fallback.answer(persona, question, seed=seed)
        instructions = (
            "You are generating fictional data for questionnaire prototyping. "
            "Write one short first-person answer in Chinese. Do not claim the "
            "answer is from a real person. Do not add personal identifiers."
        )
        prompt = json.dumps({
            "question": question.text,
            "fictional_profile": {"segment": persona.segment, "age": persona.age,
                                  "occupation": persona.occupation, "major": persona.major,
                                  "industry": persona.industry, "interests": persona.interests},
            "seed": seed,
        }, ensure_ascii=False)
        try:
            return "[模拟回答] " + self._response(self.model, instructions, prompt)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            self.disabled_reason = f"API unavailable: {type(exc).__name__}: {str(exc)[:200]}; remaining answers used local fallback"
            self.fallback_count += 1
            return self.fallback.answer(persona, question, seed=seed)

    def review(self, persona: Persona, question: Question, answer: str, *, seed: int) -> str:
        if not self.available or self.disabled_reason:
            self.review_fallback_count += 1
            self.last_review_error = self.disabled_reason or "OPENAI_API_KEY is not configured"
            return self.fallback.review(persona, question, answer, seed=seed)
        instructions = (
            "Review a fictional survey answer for coherence with its fictional "
            "profile and question. Reply in one brief Chinese sentence. This is "
            "a quality check, not empirical validation."
        )
        prompt = json.dumps({
            "question": question.text, "segment": persona.segment,
            "age": persona.age, "answer": answer, "seed": seed,
        }, ensure_ascii=False)
        try:
            return self._response(self.review_model, instructions, prompt)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            self.review_fallback_count += 1
            self.last_review_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            return "复核请求失败；未进行模型复核。"
