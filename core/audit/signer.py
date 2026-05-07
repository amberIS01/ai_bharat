"""pyHanko-based PAdES-B signer for the Day-6 evidence PDF.

Why PAdES-B (basic) and not B-LTA (long-term archival): B-LTA requires a
network round-trip to a Time Stamp Authority (TSA) on every signature, which
adds an external dependency the demo cannot rely on at the venue. The
production roadmap (deck slide 7) calls for B-LTA against a govt-of-India TSA
once Praman is wired into a CCA-licensed Class-3 DSC.

The signer:
  * Loads `deploy/demo_signer.p12` (auto-generates if missing).
  * Appends a signature field on the LAST page of the input PDF (where the
    sign-off frame is rendered).
  * Signs with `subfilter=PADES`, `embed_validation_info=False`,
    `use_pades_lta=False`.
  * Stamps a visible signature panel: signer CN + timestamp + DEMO badge.
  * Returns the signed PDF path.

The signature covers every byte of the PDF, including the head-hash line
on the cover page. Tampering with the audit-chain head hash inside the PDF
breaks the signature; tampering with any audit entry on disk breaks the
chain-verify step. Both are independent attack surfaces, both are caught.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import fitz  # PyMuPDF — used only to get page dimensions
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import fields, signers
from pyhanko.sign.fields import SigFieldSpec, SigSeedSubFilter
from pyhanko import stamp

from core.audit.cert_gen import (
    DEFAULT_P12_PATH,
    DEFAULT_PASSWORD,
    generate_demo_signing_cert,
)


SIGNATURE_FIELD_NAME = "PramanOfficerSignature"


def _page_size(pdf_path: Path) -> tuple[int, float, float]:
    """Return (last_page_index_0_based, page_width_pts, page_height_pts)."""
    with fitz.open(str(pdf_path)) as pdf:
        last = pdf.page_count - 1
        page = pdf.load_page(last)
        return last, float(page.rect.width), float(page.rect.height)


def sign_evidence_pdf(
    input_path: Path | str,
    output_path: Path | str,
    *,
    p12_path: Path | str | None = None,
    password: bytes = DEFAULT_PASSWORD,
    signer_label: str = "Praman Demo Officer (DEMO — self-signed X.509)",
) -> Path:
    """Sign `input_path` with PAdES-B and write to `output_path`."""
    input_path = Path(input_path)
    output_path = Path(output_path)
    p12_path = Path(p12_path) if p12_path else Path(DEFAULT_P12_PATH)

    # 1) Make sure we have a cert. Auto-generate on first run.
    if not p12_path.exists():
        generate_demo_signing_cert(p12_path, password=password)

    # 2) Load the signer.
    signer = signers.SimpleSigner.load_pkcs12(
        pfx_file=str(p12_path), passphrase=password,
    )

    # 3) Place the visible signature widget at the bottom of the LAST page,
    #    underneath the rendered "[pyHanko visible signature appearance ...]"
    #    placeholder text in evidence_pdf.html.
    last_page_idx, page_w, page_h = _page_size(input_path)
    # Bottom-right corner of last page, ~5cm wide × ~3cm tall.
    box = (
        page_w - 200,   # x1 — 200pt from right
        80,             # y1 — 80pt from bottom
        page_w - 30,    # x2 — 30pt from right
        180,            # y2 — 180pt from bottom (i.e. 100pt tall)
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with input_path.open("rb") as inf:
        w = IncrementalPdfFileWriter(inf)
        # Append the signature field with a visible widget on the last page.
        fields.append_signature_field(
            w,
            sig_field_spec=SigFieldSpec(
                sig_field_name=SIGNATURE_FIELD_NAME,
                box=box,
                on_page=last_page_idx,
            ),
        )

        meta = signers.PdfSignatureMetadata(
            field_name=SIGNATURE_FIELD_NAME,
            md_algorithm="sha256",
            subfilter=SigSeedSubFilter.PADES,
            # Demo: no TSA, no validation context. Production (deck) =
            # embed_validation_info=True + use_pades_lta=True + TSA URL.
            embed_validation_info=False,
            use_pades_lta=False,
            reason=f"Praman tender-evaluation evidence — signed by {signer_label}",
            location="Praman demo (local laptop)",
        )

        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        stamp_style = stamp.TextStampStyle(
            stamp_text=(
                f"DEMO SIGNATURE\n"
                f"Signer: {signer_label}\n"
                f"Time:   {ts}\n"
                f"Profile: PAdES-B (self-signed)"
            ),
            border_width=1,
        )

        pdf_signer = signers.PdfSigner(
            meta,
            signer=signer,
            stamp_style=stamp_style,
        )

        with output_path.open("wb") as outf:
            pdf_signer.sign_pdf(w, output=outf)

    return output_path


# ---------------------------------------------------------------------------
# Verification helper — used by tests and the Day-6 acceptance gate.
# ---------------------------------------------------------------------------

def verify_signed_pdf(pdf_path: Path | str) -> dict:
    """Validate the embedded PAdES signature and return a small status dict.

    The dict has keys:
      * `valid`            : bool — overall signature validity
      * `signature_intact` : bool — bytes are unmodified since signing
      * `signer_subject`   : str  — RFC 4514 subject DN
      * `signed_at`        : str | None — signing time (best effort)
      * `errors`           : list[str]
    """
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature

    pdf_path = Path(pdf_path)
    try:
        f = pdf_path.open("rb")
    except OSError as e:
        return {
            "valid": False,
            "signature_intact": False,
            "signer_subject": "",
            "signed_at": None,
            "errors": [f"Could not open PDF: {e}"],
        }
    with f:
        try:
            reader = PdfFileReader(f)
            embedded = reader.embedded_signatures
        except Exception as e:  # noqa: BLE001
            # PyHanko raises PdfStrictReadError / various subclasses on
            # malformed PDFs. Treat any parse failure as "not a signed PDF".
            return {
                "valid": False,
                "signature_intact": False,
                "signer_subject": "",
                "signed_at": None,
                "errors": [f"PDF parse failed: {type(e).__name__}: {e}"],
            }
        if not embedded:
            return {
                "valid": False,
                "signature_intact": False,
                "signer_subject": "",
                "signed_at": None,
                "errors": ["No embedded signatures found"],
            }
        sig = embedded[0]
        # Validate WITHOUT a CA trust check (self-signed cert).
        # `bottom_line` shows whether the bytes are intact even on a
        # self-signed signature; cert-trust is reported separately.
        try:
            status = validate_pdf_signature(sig)
        except Exception as e:  # noqa: BLE001
            return {
                "valid": False,
                "signature_intact": False,
                "signer_subject": "",
                "signed_at": None,
                "errors": [f"validate_pdf_signature raised: {e}"],
            }

        signer_cert = status.signing_cert
        subject = signer_cert.subject.human_friendly if signer_cert else ""
        signed_at = (
            status.signer_reported_dt.isoformat()
            if getattr(status, "signer_reported_dt", None)
            else None
        )
        return {
            "valid": bool(status.intact and status.valid),
            "signature_intact": bool(status.intact),
            "signer_subject": subject,
            "signed_at": signed_at,
            "errors": [],
        }
