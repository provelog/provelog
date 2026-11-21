"""RFC 3161 backend selection: native first, pure-Python fallback second.

The verifier must keep working when ``rfc3161_client``'s native extension is
blocked by host policy. This module hides the choice:

  * native: ``rfc3161_client`` + ``tsa_der`` (fast, current behaviour);
  * pure:   ``asn1crypto`` + ``cryptography`` (no native RFC 3161 code);
  * none:   raise BackendUnavailable -> CANNOT VERIFY, never TAMPERING.

Only the two operations the verifier needs are exposed: decode a token and
verify it against a root.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


class BackendUnavailable(Exception):
    """No RFC 3161 decoder/verifier can run in this interpreter."""


class TokenError(ValueError):
    """The token is malformed or does not verify."""


class _Alg:
    """Look enough like rfc3161_client's hash_algorithm for verify.py."""

    def __init__(self, name: str):
        self._name = name
        self.dotted_string = ""

    @property
    def name(self) -> str:
        return self._name


class _Imprint:
    def __init__(self, name: str, message: bytes):
        self.hash_algorithm = _Alg(name)
        self.message = message


class _Info:
    def __init__(self, name: str, message: bytes, gen_time: datetime):
        self.message_imprint = _Imprint(name, message)
        self.gen_time = gen_time


@dataclass
class Token:
    kind: str                                    
    raw: bytes
    tst_info: Any
    native: Any = None                                                          
    decoded: Any = None                                                   


def decode(raw: bytes) -> Token:
    """Decode a token, preferring native. Raises BackendUnavailable if neither runs."""
    try:
        from .tsa_der import decode_timestamp_response
        native = decode_timestamp_response(raw)
        return Token("native", raw, native.tst_info, native=native)
    except (ImportError, ModuleNotFoundError, OSError) as native_exc:
        try:
            from . import tsa_pure
            decoded = tsa_pure.parse(raw)
        except (ImportError, ModuleNotFoundError, OSError) as pure_exc:
            raise BackendUnavailable(
                f"native: {native_exc}; pure: {pure_exc}") from pure_exc
        info = _Info(decoded.imprint_algorithm, decoded.imprint, decoded.gen_time)
        return Token("pure", raw, info, decoded=decoded)


def verify_token(token: Token, ca_cert: Any, root_bytes: bytes) -> None:
    """Verify signature + chain to ``ca_cert``; raise TokenError on failure."""
    if token.kind == "native":
        try:
            from rfc3161_client import VerifierBuilder
        except (ImportError, ModuleNotFoundError, OSError):
                                                                             
                                                                            
            token = Token("pure", token.raw, token.tst_info)
            verify_token(token, ca_cert, root_bytes)
            return
        try:
            verifier = VerifierBuilder().add_root_certificate(ca_cert).build()
            if not verifier.verify_message(token.native, root_bytes):
                raise TokenError("RFC3161 signature or certificate chain did not validate")
            return
        except Exception as native_exc:
                                                                            
                                                                        
                                                                            
                                                
            try:
                from cryptography.hazmat.primitives import serialization

                from . import tsa_pure
                root_pem = ca_cert.public_bytes(serialization.Encoding.PEM)
                tsa_pure.verify(token.raw, root_pem, root_bytes)
                return
            except Exception:
                if isinstance(native_exc, TokenError):
                    raise
                raise TokenError(str(native_exc)) from native_exc

    from cryptography.hazmat.primitives import serialization

    from . import tsa_pure
    try:
        root_pem = ca_cert.public_bytes(serialization.Encoding.PEM)
    except AttributeError:
        root_pem = bytes(ca_cert)
    tsa_pure.verify(token.raw, root_pem, root_bytes)


def probe() -> tuple[str, str]:
    """Return (kind, detail): kind is native / pure / unavailable."""
    try:
        from rfc3161_client import VerifierBuilder  # noqa: F401

        from .tsa_der import decode_timestamp_response
        try:
            decode_timestamp_response(b"")                                       
        except (ImportError, ModuleNotFoundError, OSError):
            raise
        except Exception:
            pass
    except (ImportError, ModuleNotFoundError, OSError) as native_exc:
        try:
            from . import tsa_pure
            tsa_pure.available()
            return "pure", str(native_exc)
        except (ImportError, ModuleNotFoundError, OSError) as pure_exc:
            return "unavailable", f"native: {native_exc}; pure: {pure_exc}"
        except Exception as pure_exc:  # pragma: no cover - environment-dependent
            return "unavailable", f"native: {native_exc}; pure: {pure_exc}"
    return "native", ""
