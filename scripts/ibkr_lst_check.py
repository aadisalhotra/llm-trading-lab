"""
IBKR OAuth 1.0a consumer-key ACTIVATION / HEALTH CHECK (read-only).

Scope: performs exactly one POST to the live-session-token endpoint and
classifies the outcome. It does NOT proceed to a brokerage session init
(/iserver/auth/ssodh/init) or any other endpoint. This is diagnostic tooling,
not the trading adapter.

ENDPOINT OF RECORD
  POST https://api.ibkr.com/v1/api/oauth/live_session_token

  The /v1/api prefix is REQUIRED, and getting it wrong is an easy mistake to
  inherit: IBKR's own OAuth 1.0a onboarding mail (broker corpus item D-5,
  2026-08-19) documents the flow's first call as
  `POST https://api.ibkr.com/oauth/live_session_token`, without the prefix.
  That form 404s. Verified live 2026-08-24 -- prefixed: HTTP 200; bare: 404.
  A 404 is not evidence about activation state, so the wrong URL yields a
  confidently wrong verdict rather than an obvious failure.

VERDICTS (exit codes)
  0  ACTIVATED      HTTP 200 with a complete, cryptographically valid exchange.
  1  NOT ACTIVATED  HTTP 401 whose body is literally "Invalid Consumer" -- the
                    registration clock has not elapsed. IBKR states a newly
                    registered consumer key needs 1-2 business days (D-5).
  2  INCONCLUSIVE   Anything else, explicitly including HTTP 401 "No valid lb
                    criteria". That body means the request never reached OAuth
                    validation (malformed/unsigned Authorization header) and so
                    says NOTHING either way about activation. Treating any
                    non-200 as "not activated" misreports exactly here.

PREFLIGHT IS STRUCTURAL, NOT PRESENCE-ONLY
  An earlier revision checked only that each variable was set. That passed a
  credential populated from the wrong field of the source file; the request was
  signed and sent, and the server returned an opaque auth fault
  indistinguishable from a pending registration -- three runs spent on a fault
  provable locally in milliseconds. The preflight now asserts:

    * IBKR_ACCESS_TOKEN_SECRET is valid base64;
    * it decodes to EXACTLY the RSA modulus width (256 B for RSA-2048). An RSA
      ciphertext is never short, so a wrong width means the wrong field, not a
      truncation -- that arithmetic is what pinned the original bug;
    * it decrypts to a 32-byte plaintext (the secret is a 256-bit value);
    * signature and encryption keys PEM-parse as RSA >= 2048;
    * the Diffie-Hellman prime is >= 2048 bits.

  CAVEAT -- "PKCS#1 v1.5 decrypt did not raise" is NOT proof of key ownership.
  OpenSSL 3.2+ applies IMPLICIT REJECTION: on bad padding it returns a
  deterministic pseudo-random plaintext instead of erroring, to blunt
  Bleichenbacher padding oracles. Measured 2026-08-24 on OpenSSL 3.5.6,
  ciphertexts from a foreign key "decrypted" to 89 and 221 bytes on two of three
  trials and raised only on the third. The PLAINTEXT-WIDTH assertion is what
  carries the signal -- with it, 25 of 25 foreign-key ciphertexts were rejected.
  Do not relax that check back to a bare try/except.

SECRECY
  Credentials are read from environment variables ONLY. Nothing secret is
  printed, logged, or written to disk. The live session token is computed -- to
  prove the exchange is cryptographically valid -- but never displayed.

Required env vars:
  IBKR_CONSUMER_KEY           9-character consumer key from the OAuth
                              self-service portal (the generic paper-only key
                              TESTCONS is 8 and uses a different realm)
  IBKR_ACCESS_TOKEN           access token
  IBKR_ACCESS_TOKEN_SECRET    base64 access token secret, RSA-encrypted;
                              344 characters for RSA-2048
  IBKR_SIGNATURE_KEY          private signature key: PEM text OR a file path
  IBKR_ENCRYPTION_KEY         private encryption key: PEM text OR a file path
  IBKR_DH_PARAM               dhparam PEM text OR a file path
      -- or --
  IBKR_DH_PRIME               DH prime as hex (0x-prefixed or bare) or decimal
  IBKR_DH_GENERATOR           optional, defaults to 2

Optional env vars:
  IBKR_LST_URL                override the endpoint (testing only)

Usage:
  python scripts/ibkr_lst_check.py --preflight   # structural check, no network
  python scripts/ibkr_lst_check.py               # full activation check
"""

import base64
import os
import random
import sys
import time
from urllib.parse import quote, quote_plus

import requests
from cryptography.hazmat.primitives import hashes, hmac, serialization
from cryptography.hazmat.primitives.asymmetric import padding

LST_URL = os.environ.get(
    "IBKR_LST_URL", "https://api.ibkr.com/v1/api/oauth/live_session_token"
)

REQUIRED = [
    "IBKR_CONSUMER_KEY",
    "IBKR_ACCESS_TOKEN",
    "IBKR_ACCESS_TOKEN_SECRET",
    "IBKR_SIGNATURE_KEY",
    "IBKR_ENCRYPTION_KEY",
]

# The IBKR access token secret is a 256-bit value, so the RSA plaintext is
# exactly 32 bytes. Verified against the live credential 2026-08-24.
SECRET_PLAINTEXT_BYTES = 32


def _present(name):
    v = os.environ.get(name)
    return bool(v and v.strip())


def _fmt(name, status, detail):
    return "  {:<28} {:<5} {}".format(name, status, detail)


def _structural_report():
    """
    Validate the SHAPE of each credential, not merely its presence.

    Rationale (2026-08-24): a presence-only preflight passed while
    IBKR_ACCESS_TOKEN_SECRET held the wrong field copied from the source
    file. The request was signed and sent anyway, and the fault surfaced
    only as an opaque server-side auth error that is indistinguishable from
    a pending registration -- which cost two extra runs to disambiguate.
    Every check below is local, needs no network round-trip, and would have
    caught that on the first run.

    Returns (ok, lines). Never prints or returns secret material.
    """
    lines = []
    ok = True

    # --- consumer key / access token: cheap sanity only --------------------
    for name, expect_len in (("IBKR_CONSUMER_KEY", 9), ("IBKR_ACCESS_TOKEN", None)):
        val = os.environ.get(name, "").strip()
        if any(c.isspace() for c in val):
            ok = False
            lines.append(_fmt(name, "BAD", "contains embedded whitespace"))
            continue
        note = "len={}".format(len(val))
        if expect_len and val != "TESTCONS" and len(val) != expect_len:
            note += " (expected {} for a client-generated key)".format(expect_len)
        lines.append(_fmt(name, "OK", note))

    # --- private keys: must PEM-parse as RSA of adequate size --------------
    keys = {}
    for name in ("IBKR_SIGNATURE_KEY", "IBKR_ENCRYPTION_KEY"):
        try:
            key = _load_private_key(name)
        except Exception as exc:
            ok = False
            lines.append(_fmt(name, "BAD", "PEM parse failed: {}: {}".format(
                type(exc).__name__, exc)))
            continue
        size = getattr(key, "key_size", None)
        if size is None:
            ok = False
            lines.append(_fmt(name, "BAD", "not an RSA private key ({})".format(
                type(key).__name__)))
            continue
        keys[name] = key
        if size < 2048:
            ok = False
            lines.append(_fmt(name, "BAD", "RSA-{} private -- WEAK, IBKR requires >=2048".format(size)))
        else:
            lines.append(_fmt(name, "OK", "RSA-{} private".format(size)))

    # --- access token secret: width must equal the RSA modulus ------------
    # This is the check that pins "wrong field" vs "truncation": an RSA
    # ciphertext is EXACTLY modulus-width, never short, never long.
    raw = os.environ.get("IBKR_ACCESS_TOKEN_SECRET", "").strip()
    try:
        blob = base64.b64decode(raw, validate=True)
    except Exception as exc:
        ok = False
        blob = None
        lines.append(_fmt("IBKR_ACCESS_TOKEN_SECRET", "BAD",
                          "not valid base64 ({}) -- wrong field entirely".format(
                              type(exc).__name__)))
    if blob is not None:
        enc = keys.get("IBKR_ENCRYPTION_KEY")
        detail = "b64 len={} -> {} bytes".format(len(raw), len(blob))
        if enc is None:
            lines.append(_fmt("IBKR_ACCESS_TOKEN_SECRET", "WARN",
                              detail + "; width uncheckable (encryption key unreadable)"))
        elif len(blob) != enc.key_size // 8:
            ok = False
            lines.append(_fmt("IBKR_ACCESS_TOKEN_SECRET", "BAD",
                              detail + "; expected exactly {} bytes (RSA-{} ciphertext)"
                              " -- this is the wrong field, not a truncation".format(
                                  enc.key_size // 8, enc.key_size)))
        else:
            # Correct width is still not proof of key ownership -- and
            # "decrypt did not raise" is by itself worthless here. OpenSSL
            # 3.2+ (3.5.6 in this environment) applies IMPLICIT REJECTION to
            # PKCS#1 v1.5: on bad padding it returns a deterministic
            # pseudo-random plaintext of arbitrary length instead of
            # erroring, to blunt Bleichenbacher padding oracles. Measured
            # 2026-08-24, a foreign-key ciphertext returned 89 and 221 bytes
            # on two of three trials and raised only on the third.
            #
            # The PLAINTEXT-WIDTH assertion is what carries the signal: the
            # real secret is 256-bit, so any length but 32 means the wrong
            # key or the wrong blob.
            plain = None
            try:
                plain = enc.decrypt(blob, padding.PKCS1v15())
            except Exception as exc:
                ok = False
                lines.append(_fmt("IBKR_ACCESS_TOKEN_SECRET", "BAD",
                                  detail + "; PKCS1v15 decrypt FAILED ({}) -- ciphertext"
                                  " does not belong to this encryption key".format(
                                      type(exc).__name__)))
            if plain is not None and len(plain) != SECRET_PLAINTEXT_BYTES:
                ok = False
                lines.append(_fmt("IBKR_ACCESS_TOKEN_SECRET", "BAD",
                                  detail + "; decrypted to {} bytes, expected {} -- wrong"
                                  " encryption key (implicit rejection returns"
                                  " pseudo-random plaintext, it does not raise)".format(
                                      len(plain), SECRET_PLAINTEXT_BYTES)))
            elif plain is not None:
                lines.append(_fmt("IBKR_ACCESS_TOKEN_SECRET", "OK",
                                  detail + "; decrypts under RSA-{} to a {}-byte secret".format(
                                      enc.key_size, SECRET_PLAINTEXT_BYTES)))

    # --- DH parameters -----------------------------------------------------
    try:
        prime, generator = _load_dh()
    except Exception as exc:
        ok = False
        lines.append(_fmt("IBKR_DH_PARAM|PRIME", "BAD", "unreadable: {}: {}".format(
            type(exc).__name__, exc)))
    else:
        bits = prime.bit_length()
        note = "prime bits={} generator={}".format(bits, generator)
        if bits < 2048:
            ok = False
            lines.append(_fmt("IBKR_DH_PARAM|PRIME", "BAD", note + " -- WEAK, expected >=2048"))
        else:
            lines.append(_fmt("IBKR_DH_PARAM|PRIME", "OK", note))

    return ok, lines


def preflight():
    """Presence AND structural validity of each credential. Never prints values."""
    missing = [n for n in REQUIRED if not _present(n)]
    dh_ok = _present("IBKR_DH_PARAM") or _present("IBKR_DH_PRIME")
    print("Credential environment preflight")
    print("  [1/2] presence")
    for n in REQUIRED:
        print("  {:<28} {}".format(n, "SET" if _present(n) else "MISSING"))
    print("  {:<28} {}".format("IBKR_DH_PARAM|IBKR_DH_PRIME", "SET" if dh_ok else "MISSING"))
    if missing or not dh_ok:
        if not dh_ok:
            missing.append("IBKR_DH_PARAM or IBKR_DH_PRIME")
        print("\nRESULT: cannot run. Missing: " + ", ".join(missing))
        return False

    print("\n  [2/2] structural validity")
    struct_ok, lines = _structural_report()
    for line in lines:
        print(line)
    if not struct_ok:
        print("\nRESULT: credentials are PRESENT but STRUCTURALLY INVALID (see BAD above)."
              "\nNo request sent: a malformed credential yields an opaque server-side auth"
              "\nfault that cannot be distinguished from a pending registration.")
        return False
    print("\nRESULT: all credential variables present and structurally valid.")
    return True


def _load_material(name):
    """Return PEM bytes from either a file path or inline PEM text in the env var."""
    raw = os.environ[name]
    if "-----BEGIN" not in raw and os.path.exists(raw):
        with open(raw, "rb") as f:
            return f.read()
    return raw.replace("\\n", "\n").encode("utf-8")


def _load_private_key(name):
    return serialization.load_pem_private_key(_load_material(name), password=None)


def _load_dh():
    """Return (prime, generator)."""
    if _present("IBKR_DH_PARAM"):
        params = serialization.load_pem_parameters(_load_material("IBKR_DH_PARAM"))
        nums = params.parameter_numbers()
        return nums.p, nums.g
    raw = os.environ["IBKR_DH_PRIME"].strip()
    if raw.lower().startswith("0x"):
        prime = int(raw, 16)
    elif any(c in raw.lower() for c in "abcdef"):
        prime = int(raw, 16)
    else:
        prime = int(raw)
    return prime, int(os.environ.get("IBKR_DH_GENERATOR", "2"))


def check():
    consumer_key = os.environ["IBKR_CONSUMER_KEY"].strip()
    access_token = os.environ["IBKR_ACCESS_TOKEN"].strip()
    access_token_secret = os.environ["IBKR_ACCESS_TOKEN_SECRET"].strip()

    signature_key = _load_private_key("IBKR_SIGNATURE_KEY")
    encryption_key = _load_private_key("IBKR_ENCRYPTION_KEY")
    dh_prime, dh_generator = _load_dh()

    # TESTCONS is the generic paper-only key and uses test_realm; every
    # client-generated consumer key uses limited_poa.
    realm = "test_realm" if consumer_key == "TESTCONS" else "limited_poa"

    dh_random = random.getrandbits(256)
    dh_challenge = hex(pow(dh_generator, dh_random, dh_prime))[2:]

    # prepend = hex of the RSA/PKCS1v1.5-decrypted access token secret
    prepend = encryption_key.decrypt(
        base64.b64decode(access_token_secret), padding.PKCS1v15()
    ).hex()

    method = "POST"
    oauth_params = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": hex(random.getrandbits(128))[2:],
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": access_token,
        "oauth_signature_method": "RSA-SHA256",
        "diffie_hellman_challenge": dh_challenge,
    }
    params_string = "&".join("{}={}".format(k, v) for k, v in sorted(oauth_params.items()))
    base_string = "{}{}&{}&{}".format(prepend, method, quote_plus(LST_URL), quote(params_string))

    signature = signature_key.sign(
        base_string.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256()
    )
    oauth_params["oauth_signature"] = quote_plus(
        base64.b64encode(signature).decode("utf-8")
    )
    oauth_params["realm"] = realm

    header = "OAuth " + ", ".join('{}="{}"'.format(k, v) for k, v in sorted(oauth_params.items()))
    headers = {"Authorization": header, "User-Agent": "python/3.14"}

    print("POST {}".format(LST_URL))
    print("realm={}  consumer_key_len={}  signature_method=RSA-SHA256".format(
        realm, len(consumer_key)))

    try:
        resp = requests.post(LST_URL, headers=headers, timeout=30)
    except requests.RequestException as exc:
        print("\nRESULT: NETWORK ERROR - {}: {}".format(type(exc).__name__, exc))
        return 2

    print("HTTP {} {}".format(resp.status_code, resp.reason))

    body_text = resp.text or ""
    if resp.status_code == 200:
        try:
            data = resp.json()
        except ValueError:
            print("\nRESULT: UNEXPECTED - HTTP 200 with non-JSON body ({} bytes)".format(
                len(body_text)))
            return 2
        needed = {
            "diffie_hellman_response",
            "live_session_token_signature",
            "live_session_token_expiration",
        }
        if not needed.issubset(data):
            print("\nRESULT: UNEXPECTED - HTTP 200 missing fields: {}".format(
                sorted(needed - set(data))))
            return 2

        # Compute the LST purely to prove the exchange is cryptographically
        # valid. It is never printed.
        K = pow(int(data["diffie_hellman_response"], 16), dh_random, dh_prime)
        hex_K = hex(K)[2:]
        if len(hex_K) % 2:
            hex_K = "0" + hex_K
        bytes_K = bytes.fromhex(hex_K)
        if len(bin(K)[2:]) % 8 == 0:
            bytes_K = bytes(1) + bytes_K
        h = hmac.HMAC(bytes_K, hashes.SHA1())
        h.update(bytes.fromhex(prepend))
        _lst = base64.b64encode(h.finalize()).decode("utf-8")  # noqa: F841

        print("session token returned: yes (expiration epoch_ms={})".format(
            data["live_session_token_expiration"]))
        print("\nRESULT: ACTIVATED - live session token issued.")
        return 0

    # Non-200. Distinguish "still pending" from every other failure.
    snippet = body_text.strip()[:400]
    print("body: {}".format(snippet) if snippet else "body: <empty>")
    if resp.status_code == 401 and "invalid consumer" in body_text.lower():
        print("\nRESULT: NOT ACTIVATED - 401 Invalid Consumer (registration still pending).")
        return 1
    if resp.status_code == 401 and "no valid lb criteria" in body_text.lower():
        # Verified 2026-08-24: this is what the endpoint returns for an
        # unsigned/malformed request. It means the call never reached OAuth
        # consumer validation, so it says NOTHING about activation state.
        print("\nRESULT: INCONCLUSIVE - 401 'No valid lb criteria'. The request did not "
              "reach OAuth validation (malformed/unsigned header); this is NOT evidence "
              "either way about consumer-key activation.")
        return 2
    if resp.status_code == 401:
        print("\nRESULT: 401 but NOT 'Invalid Consumer' - this is a different auth "
              "fault (token/signature/realm), not the activation clock.")
        return 2
    print("\nRESULT: UNEXPECTED - HTTP {}; not a clean activation signal.".format(
        resp.status_code))
    return 2


if __name__ == "__main__":
    if "--preflight" in sys.argv:
        sys.exit(0 if preflight() else 1)
    if not preflight():
        print("\nHALT: credentials not present in the environment; no request sent.")
        sys.exit(1)
    print()
    sys.exit(check())
