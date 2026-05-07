"""Praman core domain models.

Tables:
    Document         — every uploaded artefact (RFP or one of the bidder annexures).
    Block            — one detected text region with bbox, source, confidence.
    Criterion        — one extracted eligibility criterion from an RFP.
    Fact             — one (criterion, bidder) pair the LLM found in bidder docs.
    Verdict          — one rule-engine output: PASS / FAIL / ABSTAIN with bindings.
    OverrideRequest  — officer override of a verdict.
    AuditEntry       — append-only Merkle-chained audit log.
    GeminiCallCache  — response cache keyed by (prompt_sha256, image_sha256, model).

Design notes (carried over from the approved Day-2 plan):
    * Block.bbox is stored as four floats + coord_origin so we can faithfully
      represent both Docling's BOTTOMLEFT (PDF native) and Gemini Vision's
      TOPLEFT (image native) frames without forcing a conversion at write time.
    * Block.confidence defaults to 1.0 for DOCLING blocks (digital text is
      assumed perfectly read) and is the model-elicited score for GEMINI_VISION
      blocks (0.0-1.0, < OCR_CONFIDENCE_THRESHOLD routes the criterion to
      ABSTAIN downstream).
    * GeminiCallCache lets us re-run the pipeline 100x in dev without burning
      $300 of API credit.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


# ---------------------------------------------------------------------------
# Enums (TextChoices keep the on-disk values stable + readable)
# ---------------------------------------------------------------------------

class DocumentType(models.TextChoices):
    RFP = "RFP", "Request for Proposal (tender notice)"
    BIDDER_COVER = "BIDDER_COVER", "Bidder cover letter / tender acceptance"
    BIDDER_AUDIT = "BIDDER_AUDIT", "Bidder audited financial summary"
    BIDDER_GST = "BIDDER_GST", "Bidder GST registration certificate"
    BIDDER_DSC = "BIDDER_DSC", "Bidder Class-3 DSC certificate"
    BIDDER_EXPERIENCE = "BIDDER_EXPERIENCE", "Bidder past performance statement"
    BIDDER_ISO = "BIDDER_ISO", "Bidder ISO 9001 certificate"
    BIDDER_OTHER = "BIDDER_OTHER", "Bidder document (other)"


class BlockSource(models.TextChoices):
    DOCLING = "DOCLING", "Docling (digital PDF parser)"
    GEMINI_VISION = "GEMINI_VISION", "Gemini Vision (image OCR fallback)"


class CoordOrigin(models.TextChoices):
    BOTTOMLEFT = "BOTTOMLEFT", "Bottom-left (PDF native, Docling)"
    TOPLEFT = "TOPLEFT", "Top-left (image native, Gemini Vision)"


class CriterionType(models.TextChoices):
    FINANCIAL = "financial", "Financial"
    COMPLIANCE = "compliance", "Compliance"
    TECHNICAL = "technical", "Technical"


class VerdictStatus(models.TextChoices):
    PASS = "PASS", "Pass"
    FAIL = "FAIL", "Fail"
    ABSTAIN = "ABSTAIN", "Abstain (needs human review)"


class OverrideStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"


# ---------------------------------------------------------------------------
# Document — one uploaded PDF (RFP or bidder annexure)
# ---------------------------------------------------------------------------

def document_upload_path(instance: "Document", filename: str) -> str:
    """Store under media/documents/<sha256-prefix>/<filename> to avoid collisions."""
    prefix = (instance.sha256 or "unsorted")[:8]
    return f"documents/{prefix}/{filename}"


class Document(models.Model):
    type = models.CharField(max_length=32, choices=DocumentType.choices)
    bidder_code = models.CharField(
        max_length=1, blank=True,
        help_text="A / B / C for bidder docs; blank for RFPs.",
    )
    file = models.FileField(upload_to=document_upload_path)
    original_filename = models.CharField(max_length=255, blank=True)
    sha256 = models.CharField(max_length=64, unique=True, db_index=True)
    page_count = models.PositiveIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["type", "bidder_code"]),
        ]

    def __str__(self) -> str:
        bidder = f"/{self.bidder_code}" if self.bidder_code else ""
        return f"[{self.type}{bidder}] {self.original_filename or self.file.name}"


# ---------------------------------------------------------------------------
# Block — one detected text region with bbox + provenance
# ---------------------------------------------------------------------------

class Block(models.Model):
    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="blocks"
    )
    block_index = models.PositiveIntegerField(
        help_text="Order within the document — for stable citation references.",
    )
    page_no = models.PositiveIntegerField(help_text="1-indexed.")

    # Bounding box. Semantics depend on coord_origin.
    bbox_l = models.FloatField()
    bbox_t = models.FloatField()
    bbox_r = models.FloatField()
    bbox_b = models.FloatField()
    coord_origin = models.CharField(
        max_length=16, choices=CoordOrigin.choices, default=CoordOrigin.BOTTOMLEFT
    )

    text = models.TextField(blank=True)
    confidence = models.FloatField(
        default=1.0,
        help_text="1.0 for digital-text Docling blocks; 0.0-1.0 self-elicited for Gemini Vision blocks.",
    )
    source = models.CharField(max_length=32, choices=BlockSource.choices)
    reason_if_low = models.TextField(
        blank=True,
        help_text="Set by Gemini Vision when confidence < OCR_CONFIDENCE_THRESHOLD.",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["document_id", "block_index"]
        indexes = [
            models.Index(fields=["document", "page_no"]),
            models.Index(fields=["confidence"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "block_index"],
                name="block_doc_index_unique",
            ),
        ]

    def __str__(self) -> str:
        preview = (self.text or "").strip()[:40].replace("\n", " ")
        return f"Block#{self.block_index} doc={self.document_id} p{self.page_no} '{preview}'"

    @property
    def is_low_confidence(self) -> bool:
        return self.confidence < settings.OCR_CONFIDENCE_THRESHOLD


# ---------------------------------------------------------------------------
# Criterion — one eligibility criterion extracted from an RFP
# ---------------------------------------------------------------------------

class Criterion(models.Model):
    rfp = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="criteria",
        limit_choices_to={"type": DocumentType.RFP},
    )
    code = models.CharField(max_length=16, help_text="e.g. 'C-1', 'C-2', stable id within an RFP.")
    title = models.CharField(max_length=255)
    requirement_text = models.TextField()
    type = models.CharField(max_length=32, choices=CriterionType.choices)
    mandatory = models.BooleanField(default=True)
    source_clause_block = models.ForeignKey(
        Block, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="criteria_citing_this_clause",
        help_text="Block in the RFP where this criterion was cited from.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Criterion"
        verbose_name_plural = "Criteria"  # avoid Django's default "Criterions"
        constraints = [
            models.UniqueConstraint(
                fields=["rfp", "code"], name="criterion_rfp_code_unique",
            ),
        ]
        ordering = ["rfp_id", "code"]

    def __str__(self) -> str:
        flag = "M" if self.mandatory else "O"
        return f"[{self.code}/{flag}] {self.title}"


# ---------------------------------------------------------------------------
# Fact — what the LLM extracted for a (criterion, bidder) pair
# ---------------------------------------------------------------------------

class Fact(models.Model):
    criterion = models.ForeignKey(Criterion, on_delete=models.CASCADE, related_name="facts")
    bidder_code = models.CharField(max_length=1)
    value = models.TextField(blank=True, help_text="Extracted value as a string.")
    ocr_confidence = models.FloatField(default=1.0)
    evidence_blocks = models.ManyToManyField(
        Block, related_name="facts_citing_this_block", blank=True,
    )
    raw_extraction_json = models.JSONField(
        default=dict, blank=True,
        help_text="Full structured response from Gemini for this extraction.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["criterion", "bidder_code"],
                name="fact_criterion_bidder_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["bidder_code"]),
        ]

    def __str__(self) -> str:
        v = (self.value or "")[:30]
        return f"Fact({self.criterion.code} / {self.bidder_code}) -> {v!r} @ conf={self.ocr_confidence:.2f}"


# ---------------------------------------------------------------------------
# Verdict — one OPA/Rego-decided outcome
# ---------------------------------------------------------------------------

class Verdict(models.Model):
    criterion = models.ForeignKey(Criterion, on_delete=models.CASCADE, related_name="verdicts")
    bidder_code = models.CharField(max_length=1)
    status = models.CharField(max_length=16, choices=VerdictStatus.choices)
    rule_id = models.CharField(max_length=128, help_text="Identifier of the Rego rule that fired.")
    bindings_json = models.JSONField(default=dict, blank=True)
    evidence_refs = models.ManyToManyField(
        Block, related_name="verdicts_citing_this_block", blank=True,
    )
    reason = models.TextField(blank=True)
    decided_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["criterion", "bidder_code"],
                name="verdict_criterion_bidder_unique",
            ),
        ]
        indexes = [
            models.Index(fields=["bidder_code", "status"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return f"Verdict({self.criterion.code} / {self.bidder_code}) = {self.status}"


# ---------------------------------------------------------------------------
# OverrideRequest — officer manual override of a verdict
# ---------------------------------------------------------------------------

class OverrideRequest(models.Model):
    verdict = models.ForeignKey(Verdict, on_delete=models.CASCADE, related_name="overrides")
    officer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
    )
    requested_status = models.CharField(max_length=16, choices=VerdictStatus.choices)
    reason = models.TextField()
    status = models.CharField(
        max_length=16, choices=OverrideStatus.choices, default=OverrideStatus.PENDING
    )
    ts = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-ts"]

    def __str__(self) -> str:
        return f"Override {self.verdict_id} -> {self.requested_status} ({self.status})"


# ---------------------------------------------------------------------------
# AuditEntry — append-only, hash-chained ("Merkle log" — a single chain, not a tree)
# ---------------------------------------------------------------------------

class AuditEntry(models.Model):
    seq = models.PositiveIntegerField(unique=True, db_index=True)
    prev_hash = models.CharField(max_length=64)  # hex sha256 of previous entry; "0"*64 for genesis
    payload_json = models.JSONField(default=dict)
    this_hash = models.CharField(max_length=64, unique=True)
    event_type = models.CharField(max_length=64, db_index=True)
    ts = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Audit entry"
        verbose_name_plural = "Audit entries"  # English plural; default would be "Audit entrys"
        ordering = ["seq"]

    def __str__(self) -> str:
        return f"Audit#{self.seq} {self.event_type} ts={self.ts.isoformat()}"


# ---------------------------------------------------------------------------
# GeminiCallCache — never burn quota on the same input twice
# ---------------------------------------------------------------------------

class GeminiCallCache(models.Model):
    prompt_sha256 = models.CharField(max_length=64, db_index=True)
    image_sha256 = models.CharField(
        max_length=64, blank=True, default="",
        help_text="Empty for text-only calls.",
    )
    model = models.CharField(max_length=64)
    response_json = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    hits = models.PositiveIntegerField(default=0, help_text="How many times this cached entry has been served.")

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["prompt_sha256", "image_sha256", "model"],
                name="gemini_cache_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"GeminiCache(prompt={self.prompt_sha256[:8]}, img={self.image_sha256[:8] or '-'}, model={self.model}, hits={self.hits})"
