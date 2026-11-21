"""Pure(ish)-Python RFC 3161 token decoding and CMS verification.

This is the fallback used when ``rfc3161_client``'s native extension cannot be
loaded (Windows App Control / WDAC, a blocked DLL, a broken wheel). It parses
the token with ``asn1crypto`` (pure Python) and verifies the CMS signature and
certificate chain with ``cryptography`` (already a core dependency for the
device key). If either import is unavailable, the caller gets
``BackendUnavailable`` and reports CANNOT VERIFY -- never TAMPERING.

Scope and honesty:
  * RSA PKCS#1 v1.5 and ECDSA signatures over signedAttrs / eContent;
  * messageImprint, message-digest, EKU=timeStamping, validity period;
  * chain walk from signer to the caller-supplied root;
  * no revocation check (the native path does not check it either);
  * any unexpected shape raises TokenError; nothing is ever "accepted" on error.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from .tsa_backend import BackendUnavailable


class TokenError(ValueError):
    """The token is malformed or does not verify."""


@dataclass(frozen=True)
class DecodedToken:
    raw: bytes
    gen_time: datetime
    imprint_algorithm: str
    imprint: bytes
    serial: int


_ALG_OIDS = {
    "1.3.14.3.2.26": "sha1",
    "2.16.840.1.101.3.4.2.1": "sha256",
    "2.16.840.1.101.3.4.2.2": "sha384",
    "2.16.840.1.101.3.4.2.3": "sha512",
}

_HASHES = {
    "sha1": hashlib.sha1,
    "sha224": hashlib.sha224,
    "sha256": hashlib.sha256,
    "sha384": hashlib.sha384,
    "sha512": hashlib.sha512,
}


def _imports():
    try:
        from asn1crypto import tsp
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
        from cryptography.x509.oid import ExtendedKeyUsageOID
    except Exception as exc:  # pragma: no cover - environment-dependent
        raise BackendUnavailable(str(exc)) from exc
    return (tsp, x509, hashes, serialization, ec, padding, rsa,
            ExtendedKeyUsageOID)


def available() -> bool:
    _imports()
    return True


def _tst_info(raw: bytes):
    (tsp, *_rest) = _imports()
    try:
        resp = tsp.TimeStampResp.load(raw)
    except Exception as exc:
        raise TokenError(f"not a TimeStampResp: {exc}") from exc
    status = resp["status"]["status"].native
    if status not in (0, "granted"):
        raise TokenError(f"TSA refused the request (status {status!r})")
    ci = resp["time_stamp_token"]
    if ci is None or ci.native is None:
        raise TokenError("response carries no timeStampToken")
    try:
        signed_data = ci["content"]
        eci = signed_data["encap_content_info"]
        ct = eci["content_type"]
        if ct.dotted != "1.2.840.113549.1.9.16.1.4" and ct.native != "tst_info":
            raise TokenError("timeStampToken does not encapsulate TSTInfo")
        content = eci["content"]
        tst = content.parsed
        if tst is None:
            tst = tsp.TSTInfo.load(content.contents)
    except TokenError:
        raise
    except Exception as exc:
        raise TokenError(f"cannot parse TSTInfo: {exc}") from exc
    return signed_data, eci, tst


def parse(raw: bytes) -> DecodedToken:
    _sd, _eci, tst = _tst_info(raw)
    try:
        gen_time = tst["gen_time"].native
        algo = tst["message_imprint"]["hash_algorithm"]["algorithm"].native
        imprint = tst["message_imprint"]["hashed_message"].native
        serial = tst["serial_number"].native
    except Exception as exc:
        raise TokenError(f"TSTInfo is missing required fields: {exc}") from exc
    if isinstance(gen_time, str):
        gen_time = datetime.fromisoformat(gen_time.replace("Z", "+00:00"))
    if gen_time.tzinfo is None:
        gen_time = gen_time.replace(tzinfo=UTC)
    return DecodedToken(raw, gen_time, str(algo), bytes(imprint), int(serial))


def _signer_cert(signed_data, tst, x509):
    signer_infos = signed_data["signer_infos"]
    if len(signer_infos) < 1:
        raise TokenError("SignedData has no signerInfo")
    si = signer_infos[0]
    cert_choices = signed_data["certificates"]
    certs = [c.chosen for c in cert_choices] if cert_choices is not None else []
    if not certs:
        raise TokenError("SignedData carries no certificates")
    sid = si["sid"]
    if sid.name != "issuer_and_serial_number":
        raise TokenError("signerInfo uses a subjectKeyIdentifier we do not support")
    ias = sid.chosen
    want_serial = ias["serial_number"].native
    want_issuer = ias["issuer"].native
    for cert in certs:
        if (cert.serial_number == want_serial
                and cert.issuer.native == want_issuer):
            chain = [x509.load_der_x509_certificate(c.dump()) for c in certs]
            return si, x509.load_der_x509_certificate(cert.dump()), chain
    raise TokenError("signer certificate is not in the token")


def _hash(name: str):
    key = _ALG_OIDS.get(str(name), str(name).lower())
    fn = _HASHES.get(key)
    if fn is None:
        raise TokenError(f"unsupported digest algorithm {name!r}")
    return fn


def _verify_chain(cert, pool, root, x509, hashes, ExtendedKeyUsageOID):
    now = datetime.now(UTC)
    seen = set()

    def fp(c):
        return c.fingerprint(hashes.SHA256())

                                                                
    try:
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        if ExtendedKeyUsageOID.TIME_STAMPING not in eku:
            raise TokenError("signer certificate has no timeStamping EKU")
    except TokenError:
        raise
    except Exception as exc:
        raise TokenError(f"signer certificate EKU cannot be read: {exc}") from exc
    if not (cert.not_valid_before_utc <= now <= cert.not_valid_after_utc):
        raise TokenError("signer certificate is outside its validity period")

    cur = cert
    while True:
        if fp(cur) == fp(root):
            return
        if fp(cur) in seen:
            raise TokenError("certificate chain loops")
        seen.add(fp(cur))
        parent = None
        for candidate in pool:
            if fp(candidate) == fp(cur):
                continue
            try:
                cur.verify_directly_issued_by(candidate)
            except Exception:
                continue
            parent = candidate
            break
        if parent is None:
            try:
                cur.verify_directly_issued_by(root)
                return
            except Exception as exc:
                raise TokenError(
                    "certificate chain does not reach the supplied root") from exc
        if not (parent.not_valid_before_utc <= now <= parent.not_valid_after_utc):
            raise TokenError("issuer certificate is outside its validity period")
        try:
            bc = parent.extensions.get_extension_for_class(
                x509.BasicConstraints).value
            if not bc.ca:
                raise TokenError("issuer certificate is not a CA")
        except TokenError:
            raise
        except Exception:
            pass
        cur = parent


def verify(raw: bytes, root_pem: bytes, root_bytes: bytes) -> bool:
    """Verify a token against a root. Raises TokenError; returns True."""
    (tsp, x509, hashes, _serialization, ec, padding, rsa,
     ExtendedKeyUsageOID) = _imports()
    _sd, eci, tst = _tst_info(raw)

    imprint = bytes(tst["message_imprint"]["hashed_message"].native)
    algo = str(tst["message_imprint"]["hash_algorithm"]["algorithm"].native)
    if imprint != _hash(algo)(root_bytes).digest():
        raise TokenError("messageImprint does not cover this Merkle root")

    signed_data, _eci, _tst = _tst_info(raw)
    si, signer, pool = _signer_cert(signed_data, tst, x509)

    digest_name = si["digest_algorithm"]["algorithm"].native
    hash_fn = _hash(digest_name)
    econtent = eci["content"].contents
    signed_attrs = si["signed_attrs"]
    if signed_attrs is not None and len(signed_attrs) > 0:
        md = None
        for attr in signed_attrs:
            if attr["type"].native == "message_digest":
                md = attr["values"][0].native
                break
        if md is None:
            raise TokenError("signedAttrs has no message_digest")
        if md != hash_fn(econtent).digest():
            raise TokenError("message_digest does not match eContent")
        data = b"\x31" + signed_attrs.dump()[1:]
    else:
        data = econtent

    cert = signer
    public_key = cert.public_key()
    signature = si["signature"].native
    sigalg = si["signature_algorithm"]["algorithm"].native
    crypt_hashes = {
        "sha1": hashes.SHA1, "sha224": hashes.SHA224, "sha256": hashes.SHA256,
        "sha384": hashes.SHA384, "sha512": hashes.SHA512,
    }
    hash_alg = crypt_hashes.get(str(digest_name).lower())
    if hash_alg is None:
        raise TokenError(f"unsupported signature digest {digest_name!r}")
    try:
        if isinstance(public_key, rsa.RSAPublicKey):
            if sigalg not in ("rsassa_pkcs1v15", "sha1_rsa", "sha224_rsa",
                              "sha256_rsa", "sha384_rsa", "sha512_rsa"):
                raise TokenError(f"unsupported RSA signature algorithm {sigalg!r}")
            public_key.verify(signature, data, padding.PKCS1v15(), hash_alg())
        elif isinstance(public_key, ec.EllipticCurvePublicKey):
            public_key.verify(signature, data, ec.ECDSA(hash_alg()))
        else:
            raise TokenError("unsupported signer public key type")
    except TokenError:
        raise
    except Exception as exc:
        raise TokenError(f"CMS signature did not verify: {exc}") from exc

    try:
        root = x509.load_pem_x509_certificate(root_pem)
    except Exception as exc:
        raise TokenError(f"root certificate cannot be loaded: {exc}") from exc
    _verify_chain(cert, pool, root, x509, hashes, ExtendedKeyUsageOID)
    return True
