"""Consent-gated extraction of de-identified source profiles into a panel."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import Persona
from .providers import OpenAIResponsesProvider


_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_ALLOWED_FIELDS = {"source_id", "provenance", "consent_scope", "profile_text", "deidentified"}


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    provenance: str
    consent_scope: str
    profile_text: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SourceRecord":
        if not isinstance(data, dict) or set(data) - _ALLOWED_FIELDS:
            raise ValueError("source record has unsupported fields")
        if data.get("deidentified") is not True:
            raise ValueError("each source record must explicitly set deidentified: true")
        scopes = {scope.strip() for scope in str(data.get("consent_scope", "")).split(",")}
        if not {"survey_simulation", "external_model"}.issubset(scopes):
            raise ValueError("build-panel requires survey_simulation,external_model consent_scope")
        values = {key: str(data.get(key, "")).strip() for key in ("source_id", "provenance", "consent_scope", "profile_text")}
        if any(not value for value in values.values()):
            raise ValueError("source_id, provenance, consent_scope and profile_text are required")
        if _EMAIL.search(values["profile_text"]) or _PHONE.search(values["profile_text"]):
            raise ValueError("profile_text appears to contain email or mobile number; de-identify before import")
        return cls(**values)


def read_source_records(path: Path, *, limit: int | None = None) -> list[SourceRecord]:
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    if path.suffix.lower() == ".jsonl":
        with path.open("r", encoding="utf-8") as stream:
            raw = [json.loads(line) for line in stream if line.strip()]
    else:
        with path.open("r", encoding="utf-8") as stream:
            raw = json.load(stream)
        if isinstance(raw, dict):
            raw = raw.get("records")
    if not isinstance(raw, list):
        raise ValueError("source must be a JSON/JSONL list or a JSON object with records")
    selected = raw[:limit]
    if not selected:
        raise ValueError("source records are empty")
    records = [SourceRecord.from_dict(record) for record in selected]
    if len({record.source_id for record in records}) != len(records):
        raise ValueError("source_id values must be unique")
    return records


def _public_id(source_id: str, salt: str) -> str:
    return "derived-" + hmac.new(bytes.fromhex(salt), source_id.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def _parse_json_reply(reply: str) -> dict[str, Any]:
    text = reply.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("model reply must be a JSON object")
    return value


def _fingerprint(records: list[SourceRecord]) -> str:
    # A digest binds the checkpoint to the exact selected source records while
    # keeping their private text out of the checkpoint itself.
    serialized = json.dumps([
        (record.source_id, record.provenance, record.consent_scope, record.profile_text)
        for record in records
    ], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _save_checkpoint(path: Path | None, state: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(state, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    path.chmod(0o600)


def _load_checkpoint(path: Path | None, records: list[SourceRecord],
                     provider: OpenAIResponsesProvider, review_fraction: float) -> dict[str, Any]:
    expected = {
        "schema_version": 1,
        "source_fingerprint": _fingerprint(records),
        "generation_model": provider.model,
        "review_model": provider.review_model,
        "review_fraction": review_fraction,
    }
    if path is None or not path.exists():
        return {**expected, "salt": secrets.token_hex(32), "personas": [], "reviews": [],
                "last_error": None, "complete": False}
    with path.open("r", encoding="utf-8") as stream:
        state = json.load(stream)
    if not isinstance(state, dict) or any(state.get(key) != value for key, value in expected.items()):
        raise ValueError("checkpoint does not match source records, models or review fraction")
    if not isinstance(state.get("personas"), list) or not isinstance(state.get("reviews"), list):
        raise ValueError("checkpoint is malformed")
    salt = state.get("salt")
    if not isinstance(salt, str) or len(salt) != 64:
        raise ValueError("checkpoint salt is missing or malformed")
    try:
        bytes.fromhex(salt)
    except ValueError as exc:
        raise ValueError("checkpoint salt is malformed") from exc
    if len(state["personas"]) > len(records):
        raise ValueError("checkpoint contains more profiles than the source")
    for index, item in enumerate(state["personas"]):
        persona = Persona.from_dict(item)
        if persona.id != _public_id(records[index].source_id, salt):
            raise ValueError("checkpoint profile order does not match source")
    return state


def build_panel(
    records: list[SourceRecord], provider: OpenAIResponsesProvider, *, review_fraction: float = 0.02,
    checkpoint_path: Path | None = None,
) -> tuple[list[Persona], dict[str, Any]]:
    """Extract profiles with Sol and audit a small subset with Astra.

    No raw source text or identifiers are returned. API failures stop the build.
    The audit is a model quality check, not a factual or consent verification.
    """
    if not provider.available:
        raise ValueError("OPENAI_API_KEY is required for build-panel; no paid build was run")
    if not 0 <= review_fraction <= 1:
        raise ValueError("review_fraction must be between 0 and 1")
    if not records:
        raise ValueError("records cannot be empty")
    state = _load_checkpoint(checkpoint_path, records, provider, review_fraction)
    resumed_profiles = len(state["personas"])
    personas = [Persona.from_dict(item) for item in state["personas"]]
    for index in range(len(personas), len(records)):
        record = records[index]
        try:
            reply = provider._response(
            provider.model,
            "Extract a compact structured profile from de-identified, consented source text. "
            "Return JSON only with keys segment, age, region, education, occupation, major, industry, interests, traits. "
            "Use segment student or working_adult when explicit; otherwise unknown. "
            "Do not infer missing facts: age should be null, missing strings including major/industry 未提供, interests [] and traits {}. "
            "Traits, if explicitly supported, are numbers from 0 to 1. Never include names or identifiers.",
            record.profile_text,
            max_output_tokens=800, reasoning_effort="low",
            )
            extracted = _parse_json_reply(reply)
            allowed = {"segment", "age", "region", "education", "occupation", "major", "industry", "interests", "traits"}
            if set(extracted) - allowed:
                raise ValueError("model returned unexpected profile fields")
            persona = Persona.from_dict({
                "id": _public_id(record.source_id, state["salt"]),
                "segment": extracted.get("segment") or "unknown",
                "age": extracted.get("age"),
                "region": extracted.get("region") or "未提供",
                "education": extracted.get("education") or "未提供",
                "occupation": extracted.get("occupation") or "未提供",
                "major": extracted.get("major") or "未提供",
                "industry": extracted.get("industry") or "未提供",
                "interests": extracted.get("interests") or [],
                "traits": extracted.get("traits") or {},
                "synthetic": False,
                "provenance": record.provenance,
                "consent_scope": record.consent_scope,
            })
        except Exception as exc:
            state["last_error"] = {"stage": "extract", "index": index,
                                   "type": type(exc).__name__, "message": str(exc)[:300]}
            _save_checkpoint(checkpoint_path, state)
            raise RuntimeError(f"build-panel failed at profile {index + 1}: {type(exc).__name__}: {exc}; "
                               f"completed profiles: {len(personas)}; checkpoint: {checkpoint_path}") from exc
        personas.append(persona)
        state["personas"].append(persona.to_dict())
        state["last_error"] = None
        _save_checkpoint(checkpoint_path, state)
    review_count = round(len(records) * review_fraction)
    if review_fraction and not review_count:
        review_count = 1
    review_ids = sorted(range(len(records)), key=lambda i: hashlib.sha256(
        f"review:{records[i].source_id}".encode()).digest())[:review_count]
    reviews = list(state["reviews"])
    if len(reviews) > len(review_ids) or any(
        review.get("persona_id") != personas[review_ids[position]].id
        for position, review in enumerate(reviews)
    ):
        raise ValueError("checkpoint review order does not match source")
    for position in range(len(reviews), len(review_ids)):
        index = review_ids[position]
        record, persona = records[index], personas[index]
        try:
            reply = provider._response(
            provider.review_model,
            "Audit whether the derived profile is supported by the source text. "
            "Return JSON only: {\"status\":\"pass\" or \"needs_attention\",\"note\":\"brief reason\"}. "
            "Do not repeat source text or personal identifiers. This is a model quality check, not factual validation.",
            json.dumps({"source_text": record.profile_text, "derived_profile": persona.to_dict()}, ensure_ascii=False),
            max_output_tokens=800, reasoning_effort="low",
            )
            review = _parse_json_reply(reply)
            if review.get("status") not in {"pass", "needs_attention"} or not isinstance(review.get("note"), str):
                raise ValueError("review model returned an invalid audit result")
        except Exception as exc:
            state["last_error"] = {"stage": "review", "index": index,
                                   "type": type(exc).__name__, "message": str(exc)[:300]}
            _save_checkpoint(checkpoint_path, state)
            raise RuntimeError(f"build-panel review failed for profile {index + 1}: {type(exc).__name__}: {exc}; "
                               f"completed reviews: {len(reviews)}; checkpoint: {checkpoint_path}") from exc
        # Keep the model's free-form note out of exported audit files: it can
        # inadvertently quote the private source even when instructed not to.
        reviews.append({
            "persona_id": persona.id,
            "status": review["status"],
            "note": "模型标记需人工检查。" if review["status"] == "needs_attention" else "模型抽查通过；仍需人工确认。",
        })
        state["reviews"].append(reviews[-1])
        state["last_error"] = None
        _save_checkpoint(checkpoint_path, state)
    state["complete"] = True
    _save_checkpoint(checkpoint_path, state)
    audit = {
        "notice": "Derived profiles are based on authorized sources; generated survey answers remain simulations.",
        "profiles_built": len(personas),
        "resumed_profiles": resumed_profiles,
        "reviewed_profiles": review_count,
        "generation_model": provider.model,
        "review_model": provider.review_model if review_count else None,
        "reviews": reviews,
    }
    return personas, audit
