"""Pydantic schemas Gemini fills in via `response_schema=`.

Two extraction passes:
    1. Criteria extraction (RFP → list of CriterionExtraction)  — Gemini 2.5 Pro
    2. Fact extraction     (one criterion × one bidder)         — Gemini 2.5 Flash

Both schemas ask Gemini to cite back to our own `block_index` IDs so we can
join its output to the `core.models.Block` rows the parsing layer already
persisted. `confidence` is prompt-elicited self-assessment with a one-line
`reason_if_low` whenever the model is unsure — this is what lets the Rego
policy engine emit ABSTAIN on Day 4 instead of silently failing a bidder.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Criteria extraction (RFP → eligibility criteria)
# ---------------------------------------------------------------------------

class CriterionExtraction(BaseModel):
    code: str = Field(
        description="A short stable identifier for this criterion within the RFP, e.g. 'C-1', 'C-2'."
    )
    title: str = Field(
        description="The criterion in plain English (e.g. 'Minimum Annual Turnover')."
    )
    requirement_text: str = Field(
        description="The exact requirement as stated in the RFP, kept faithful to original wording."
    )
    type: Literal["financial", "compliance", "technical"] = Field(
        description="High-level category of the criterion."
    )
    mandatory: bool = Field(
        description="True if the criterion is mandatory; False if it is preferred / optional."
    )
    source_block_ids: list[int] = Field(
        default_factory=list,
        description="block_index values from the supplied block manifest that contain the text of this criterion.",
    )


class CriteriaResult(BaseModel):
    criteria: list[CriterionExtraction] = Field(
        default_factory=list,
        description="Every distinct ELIGIBILITY CRITERION found in the RFP, in document order.",
    )


# ---------------------------------------------------------------------------
# Fact extraction (criterion × bidder → one extracted value)
# ---------------------------------------------------------------------------

class FactExtraction(BaseModel):
    found: bool = Field(
        description="True if the requested value is present in the bidder's documents."
    )
    value: str = Field(
        default="",
        description="The extracted value as a literal string (numbers as written, e.g. 'Rs. 6,80,00,000' or '29ABCDE0000F1Z5'). Empty if found=False.",
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Your confidence (0.0-1.0) that the extracted value is correct. Use < 0.85 whenever OCR noise, ambiguity, or partial information makes you uncertain.",
    )
    reason_if_low: str = Field(
        default="",
        description="One-line explanation when confidence < 0.85, e.g. 'GST cert region heavy photocopy noise', 'turnover figure ambiguous', 'value not located'. Empty otherwise.",
    )
    source_block_ids: list[int] = Field(
        default_factory=list,
        description="block_index values from the supplied bidder block manifest that contain the evidence supporting your extracted value.",
    )
    notes: str = Field(
        default="",
        description="Optional one-line note for the human reviewer. Keep it short.",
    )
