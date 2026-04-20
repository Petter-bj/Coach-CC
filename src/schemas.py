"""Pydantic-schemas for strukturerte inputs (primært strength screenshot-parse).

Brukes av `strength log`-CLI-en for å validere JSON fra Claude før insert.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class StrengthSet(BaseModel):
    reps: int = Field(..., ge=1, le=100)
    weight_kg: float | None = Field(None, ge=0, le=500)
    rpe: int | None = Field(None, ge=0, le=10)
    notes: str | None = None


class StrengthExercise(BaseModel):
    name: str = Field(..., min_length=1)
    sets: list[StrengthSet] = Field(..., min_length=1)


class StrengthSession(BaseModel):
    """Hel styrkeøkt slik Claude skal parse en screenshot."""

    started_at_local: str = Field(
        ...,
        description="ISO 8601 lokal tid, f.eks. '2026-04-19T18:30'",
    )
    exercises: list[StrengthExercise] = Field(..., min_length=1)
    session_name: str | None = Field(
        None,
        description="f.eks. 'Push', 'Pull', 'Legs', 'Upper'",
    )
    notes: str | None = None

    @field_validator("started_at_local")
    @classmethod
    def _check_iso_local(cls, v: str) -> str:
        # Aksepterer 'YYYY-MM-DDTHH:MM' eller 'YYYY-MM-DD HH:MM'
        v = v.strip().replace(" ", "T")
        # Valider at den parser
        try:
            datetime.fromisoformat(v)
        except ValueError as e:
            raise ValueError(
                f"started_at_local må være ISO 8601 (YYYY-MM-DDTHH:MM): {e}"
            )
        return v

    def total_sets(self) -> int:
        return sum(len(ex.sets) for ex in self.exercises)

    def local_date(self) -> str:
        return self.started_at_local[:10]
