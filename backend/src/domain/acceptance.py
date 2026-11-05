"""User-owned task acceptance contracts and durable verification evidence."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictInt, model_validator


class _Rule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")


class FileExistsCheck(_Rule):
    kind: Literal["file_exists"]
    path: str = Field(min_length=1, max_length=4096)


class FileContainsCheck(_Rule):
    kind: Literal["file_contains"]
    path: str = Field(min_length=1, max_length=4096)
    text: str = Field(min_length=1, max_length=16000)


class JsonEqualsCheck(_Rule):
    kind: Literal["json_equals"]
    path: str = Field(min_length=1, max_length=4096)
    expected: JsonValue


class CommandCheck(_Rule):
    kind: Literal["command"]
    cmd: str = Field(min_length=1, max_length=16000)
    timeout_seconds: StrictInt = Field(default=30, ge=1, le=120)


AcceptanceCheck = Annotated[
    FileExistsCheck | FileContainsCheck | JsonEqualsCheck | CommandCheck, Field(discriminator="kind")
]


class AcceptanceContract(BaseModel):
    """Immutable for one Turn; never exposed as a model-writable tool."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    checks: tuple[AcceptanceCheck, ...] = Field(min_length=1, max_length=16)
    max_repairs: StrictInt = Field(default=2, ge=0, le=5)
    max_model_turns: StrictInt = Field(default=32, ge=1, le=128)
    max_tool_calls: StrictInt = Field(default=128, ge=1, le=1000)
    reserved_tool_calls: StrictInt = Field(default=0, ge=0, le=1000)

    def fingerprint(self) -> str:
        canonical = json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode()).hexdigest()

    @model_validator(mode="after")
    def unique_ids(self) -> AcceptanceContract:
        if len({check.id for check in self.checks}) != len(self.checks):
            raise ValueError("Acceptance check IDs must be unique.")
        if self.reserved_tool_calls > self.max_tool_calls:
            raise ValueError("Reserved calls cannot exceed the total tool budget.")
        return self


class CheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    status: Literal["passed", "failed", "blocked", "cancelled", "budget_exhausted"]
    detail: str
    duration_ms: float = 0


class AcceptanceReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    attempt: int
    status: Literal["passed", "failed", "blocked", "cancelled", "budget_exhausted"]
    checks: tuple[CheckResult, ...]


class AcceptanceProgress(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contract: AcceptanceContract
    status: Literal["pending", "repairing", "passed", "failed", "blocked", "cancelled", "budget_exhausted"] = "pending"
    repairs: int = 0
    reports: tuple[AcceptanceReport, ...] = ()
    tool_call_ids: tuple[str, ...] = Field(default=(), max_length=1000)
    handoff_reason: Literal["agent_tool_limit", "model_turn_limit", "tool_batch_limit"] | None = None
