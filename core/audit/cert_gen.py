"""Generate (or load) the self-signed X.509 cert + RSA key used to sign the
Day-6 evidence PDF.

This is the demo signer. In production, the signer is bound to a CCA-licensed
Class-3 DSC token via PKCS#11 (eMudhra ProxKey, ePass2003, etc.). The cert
content here is deliberately marked "DEMO USE ONLY" so a procurement officer
or auditor reviewing a signed evidence PDF immediately sees that the signature
is from the local laptop rather than from a trusted government CA.

Why we keep this in-repo and gitignored: the PKCS#12 file contains a private
key. It must NEVER be committed. The `.p12` glob is in .gitignore. The
generation function is idempotent — calling it again on an existing path
returns the existing cert, no overwrite.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

# Demo defaults — stored in deploy/, gitignored (`.p12` glob in .gitignore).
DEFAULT_P12_PATH = Path("deploy/demo_signer.p12")
DEFAULT_PASSWORD = b"praman_demo_2026"

# Friendly fields embedded in the cert. The "DEMO USE ONLY" marker shows up
# in the PDF signature panel under "Signed by:".
DEFAULT_COMMON_NAME = "Praman Demo Officer"
DEFAULT_ORGANISATION = "Praman Demo (Self-Signed — DEMO USE ONLY)"
DEFAULT_COUNTRY = "IN"


def _utcnow() -> dt.datetime:
    """Timezone-aware UTC. Modern `cryptography` deprecates naive datetimes."""
    return dt.datetime.now(dt.timezone.utc)


def generate_demo_signing_cert(
    p12_path: Path | str = DEFAULT_P12_PATH,
    *,
    password: bytes = DEFAULT_PASSWORD,
    common_name: str = DEFAULT_COMMON_NAME,
    organisation: str = DEFAULT_ORGANISATION,
    country: str = DEFAULT_COUNTRY,
    validity_days: int = 365,
) -> Path:
    """Create a self-signed RSA-2048 X.509 cert and bundle it into a PKCS#12.

    Idempotent: if `p12_path` already exists, returns it unchanged.
    Otherwise: generates key + cert + serialises to PKCS#12.
    """
    p12_path = Path(p12_path)
    if p12_path.exists():
        return p12_path

    p12_path.parent.mkdir(parents=True, exist_ok=True)

    # 1) RSA-2048 private key. 4096 would be stronger but RSA-2048 is the
    #    minimum CCA India accepts and signs Class-3 DSC tokens with — using
    #    the same size keeps the demo's posture honest.
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # 2) Subject == Issuer (self-signed).
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, country),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, organisation),
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
    ])

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_utcnow())
        .not_valid_after(_utcnow() + dt.timedelta(days=validity_days))
        # Mark explicitly as a non-CA leaf cert.
        .add_extension(
            x509.BasicConstraints(ca=False, path_length=None), critical=True
        )
        # PAdES-B requires non-repudiation + digitalSignature key usage.
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=True,  # aka non-repudiation
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([
                x509.oid.ExtendedKeyUsageOID.CODE_SIGNING,
                # No EMAIL_PROTECTION — we sign documents, not email.
            ]),
            critical=False,
        )
        .sign(private_key, hashes.SHA256())
    )

    pkcs12_bytes = pkcs12.serialize_key_and_certificates(
        name=b"praman-demo-signer",
        key=private_key,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(password),
    )
    p12_path.write_bytes(pkcs12_bytes)
    return p12_path


def load_demo_cert(
    p12_path: Path | str = DEFAULT_P12_PATH,
    *,
    password: bytes = DEFAULT_PASSWORD,
):
    """Convenience: load (private_key, cert, additional_certs) from a PKCS#12."""
    p12_path = Path(p12_path)
    return pkcs12.load_key_and_certificates(
        p12_path.read_bytes(), password=password,
    )
