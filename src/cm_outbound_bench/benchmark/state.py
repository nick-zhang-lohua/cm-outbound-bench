"""Per-task initial state: identity, hashing, validation and isolation.

Every benchmark task references exactly one explicit initial state. A state is a
validated synthetic database payload with a stable identifier, a version and a
content hash. Each episode receives its own deep copy, so episode ordering can
never leak state.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cm_outbound_bench.domain.data_model import CmOutboundDB
from cm_outbound_bench.paths import data_path

STATE_VERSION = "1.0"


class StateRecord(BaseModel):
    """One declarative initial state as stored on disk."""

    model_config = ConfigDict(extra="forbid")

    state_id: str = Field(min_length=1)
    state_version: str = STATE_VERSION
    description: str = ""
    db: CmOutboundDB


class StateFile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    state_version: str = STATE_VERSION
    domain_id: str = ""
    generated_by: str = ""
    states: list[StateRecord] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_state_ids(self) -> "StateFile":
        state_ids = [state.state_id for state in self.states]
        if len(state_ids) != len(set(state_ids)):
            raise ValueError("Duplicate state_id values in state file")
        return self


def canonical_json(payload: object) -> str:
    """Return a canonical JSON representation used for hashing."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def hash_database(db: CmOutboundDB) -> str:
    """Return the stable SHA-256 of a synthetic database payload."""
    return hashlib.sha256(
        canonical_json(db.model_dump(mode="json")).encode("utf-8")
    ).hexdigest()


class InitialState:
    """A validated initial state with identity, hash and per-episode copying."""

    def __init__(self, record: StateRecord) -> None:
        self.state_id = record.state_id
        self.state_version = record.state_version
        self.description = record.description
        self.db = record.db.deep_copy()
        self.state_hash = hash_database(self.db)

    @classmethod
    def from_database(
        cls,
        state_id: str,
        db: CmOutboundDB,
        *,
        description: str = "",
        state_version: str = STATE_VERSION,
    ) -> "InitialState":
        return cls(
            StateRecord(
                state_id=state_id,
                state_version=state_version,
                description=description,
                db=db,
            )
        )

    def instantiate(self) -> CmOutboundDB:
        """Return an independent copy for one episode."""
        return self.db.deep_copy()

    def to_record(self) -> StateRecord:
        return StateRecord(
            state_id=self.state_id,
            state_version=self.state_version,
            description=self.description,
            db=self.db.deep_copy(),
        )

    def manifest(self) -> dict[str, object]:
        return {
            "state_id": self.state_id,
            "state_version": self.state_version,
            "state_hash": self.state_hash,
            "description": self.description,
            "statistics": self.db.get_statistics(),
        }


class StateStore:
    """Loads and caches declarative state files."""

    def __init__(self, states: list[StateRecord]) -> None:
        self._states = {state.state_id: InitialState(state) for state in states}

    @classmethod
    def from_file(cls, path: str | Path) -> "StateStore":
        payload = Path(path).read_text(encoding="utf-8")
        return cls(StateFile.model_validate_json(payload).states)

    @classmethod
    def from_package(cls, *parts: str) -> "StateStore":
        return cls.from_file(data_path(*parts))

    def get(self, state_id: str) -> InitialState:
        state = self._states.get(state_id)
        if state is None:
            available = ", ".join(sorted(self._states))
            raise KeyError(
                f"Unknown initial state {state_id!r}. Available states: {available}"
            )
        return state

    def has(self, state_id: str) -> bool:
        return state_id in self._states

    def list_states(self) -> list[InitialState]:
        return [self._states[key] for key in sorted(self._states)]

    def __len__(self) -> int:
        return len(self._states)


def validate_state_for_line(state: InitialState, phone_number_id: str) -> list[str]:
    """Return structural problems that would make a state unusable.

    The checks are deliberately local to the task's line so that a state can be
    reused for several tasks on the same customer without claiming relevance to
    unrelated lines.
    """
    problems: list[str] = []
    phones = {item.phone_number_id: item for item in state.db.phone_numbers}
    line = phones.get(phone_number_id)
    if line is None:
        problems.append("target_line_missing")
        return problems
    if not any(
        item.customer_id == line.owner_customer_id for item in state.db.customers
    ):
        problems.append("owner_customer_missing")
    if not any(item.plan_id == line.current_plan_id for item in state.db.plans):
        problems.append("current_plan_missing")
    if not any(
        item.phone_number_id == phone_number_id for item in state.db.subscriptions
    ):
        problems.append("subscription_missing")
    if not any(
        item.phone_number_id == phone_number_id for item in state.db.identity_records
    ):
        problems.append("identity_record_missing")
    return problems
