"""Bundled TSA trust roots for offline timestamp verification.

A trusted timestamp is only as good as the verifier's ability to confirm the token was
signed by the claimed TSA and that the TSA chains to a trusted root. We bundle the roots
for the two TSAs we anchor against (Sectigo, FreeTSA) so `provelog verify` validates the
full CMS signature and cert chain locally, with no network and no trust in our server.
Users can add their own roots.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

                                                                   
_ROOTS = {
    "freetsa.org": "freetsa_cacert.pem",
                                                                           
                                                                            
                                                                            
                                                                             
                                                       
    "timestamp.sectigo.com": "sectigo_tsa_cacert.pem",
}

_DIR = Path(__file__).parent

                                                                                        
                                                                                           
                                                                                             
_EXTRA_ROOTS: dict[str, Any] = {}


def _load_cert(cert: Any):
    """Accept a Certificate, a PEM path, or PEM bytes/str and return an x509 Certificate."""
    from cryptography import x509
    if hasattr(cert, "public_bytes"):                         
        return cert
    if isinstance(cert, (str, Path)) and Path(str(cert)).exists():
        return x509.load_pem_x509_certificate(Path(str(cert)).read_bytes())
    if isinstance(cert, str):
        return x509.load_pem_x509_certificate(cert.encode("utf-8"))
    if isinstance(cert, bytes):
        return x509.load_pem_x509_certificate(cert)
    raise ValueError("cert must be an x509 Certificate, a PEM file path, or PEM bytes/str")


def add_root(host_substring: str, cert: Any):
    """Register a TSA root the verifier will trust for any TSA URL containing host_substring.
    Returns the loaded Certificate. Operators use this (or `provelog verify --tsa-root host=cert.pem`)
    to validate timestamps from a TSA Provelog does not bundle."""
    loaded = _load_cert(cert)
    _EXTRA_ROOTS[host_substring] = loaded
    return loaded


def registered_hosts() -> list[str]:
    """The host substrings with a trusted root (operator-registered first, then bundled)."""
    return list(_EXTRA_ROOTS) + list(_ROOTS)


def root_for_tsa(tsa_url: str | None):
    """Return a cryptography x509 Certificate for the TSA, or None if no trusted root is known.
    Operator-registered roots take precedence over the bundled set."""
    if not tsa_url:
        return None
    from cryptography import x509
    for host, cert in _EXTRA_ROOTS.items():
        if host in tsa_url:
            return cert
    for host, fname in _ROOTS.items():
        if host in tsa_url:
            path = _DIR / fname
            if path.exists():
                return x509.load_pem_x509_certificate(path.read_bytes())
    return None
