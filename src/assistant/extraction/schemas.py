from typing import Literal

from pydantic import BaseModel, Field


class ExtractedItem(BaseModel):
    type: Literal["deadline", "promise", "meeting", "task", "fact", "none"]
    description: str
    due_at: str | None = None
    due_confidence: Literal["exact", "approximate", "unclear"] = "unclear"
    participants: list[str] = Field(default_factory=list)
