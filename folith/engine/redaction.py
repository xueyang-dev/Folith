"""Shared redaction for provider error text before it is shown or persisted.

CX-06 §3: a rejected request's reply (an HTTP 400 body, say) can echo the very
credential that was sent, a stretch of the user's text, and an absolute path
from this machine.  The same body used to reach the user-visible message, the
per-page failure records, the technical log and the performance summary — and
from there the full-data backup.

Every such writer now passes its text through :func:`redact_error_text`.  The
function is the identity on clean text (the existing failure-record contracts
and their tests are unchanged) and replaces only what must never be persisted:
credentials in their own syntax, ``key: value`` credential shapes, the home
prefix and account name, absolute local paths (collapsed to their last
component), and — strongest of all — the caller's own known credential,
wherever it appears.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

#: Key names whose ``key: value`` shape is a credential, whatever the value.
SECRET_KEY_NAMES = ("api_key", "apikey", "token", "authorization", "secret",
                    "password", "api-key", "x-api-key")

#: Credentials that carry their own syntax.
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),              # OpenAI-style
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),            # Google
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),               # GitHub
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),      # Slack
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{12,}"),   # Authorization: Bearer …
)

PLACEHOLDER = "<redacted>"
HOME_PLACEHOLDER = "~"
USER_PLACEHOLDER = "<user>"
EXTERNAL_PLACEHOLDER = "<external>"

#: A local absolute path: a POSIX root with two or more components, or a
#: Windows drive root.  A URL never matches (the character before its first
#: slash is ``:`` or a word character), ``~``-relative remainders are left
#: alone, and a relative path is untouched.
_LOCAL_PATH = re.compile(
    r"(?<![\w:/~])(/[\w.\-]+(?:/[\w.\-]+)+)|([A-Za-z]:\\[^\s\"'\]]+)")

#: ``key: value`` (or ``"key": "value"``) credential shapes: the value is the
#: secret, the key, separator and quoting are kept as written.
_KEY_VALUE = re.compile(
    r"(?i)\b({0})(\s*[\"']?\s*[:=]\s*[\"']?)(\"[^\"]*\"|'[^']*'|[^\s\"'{{}}\[,]+)".format(
        "|".join(re.escape(name) for name in SECRET_KEY_NAMES)))


def _externalize(token: str) -> str:
    """A local absolute path keeps only its last component."""
    parts = [part for part in re.split(r"[\\/]+", token.rstrip("\\/")) if part]
    separator = "\\" if "\\" in token else "/"
    last = parts[-1] if parts else ""
    return f"{EXTERNAL_PLACEHOLDER}{separator}{last}" if last else EXTERNAL_PLACEHOLDER


def redact_error_text(text: object, *,
                      known_secrets: Iterable[str] = (),
                      home: Optional[Path] = None) -> str:
    """Return ``text`` with credentials, the account name and local paths removed.

    ``known_secrets`` are literal values the caller owns (the connection's own
    API key): wherever one appears — even inside a provider's echoed reply —
    it is replaced.  Everything else is pattern redaction, so the function is
    safe to call on any error text and is a no-op on clean text.
    """
    redacted = "" if text is None else str(text)
    home = home or Path.home()
    try:
        home_text = str(home)
    except Exception:  # pragma: no cover - defensive
        home_text = ""
    if home_text:
        redacted = redacted.replace(home_text, HOME_PLACEHOLDER)
    user_name = home.name if home_text else ""
    if user_name:
        redacted = redacted.replace(user_name, USER_PLACEHOLDER)
    for secret in known_secrets:
        text_secret = str(secret or "").strip()
        if not text_secret:
            continue
        # Long values are replaced wherever they occur: a relay can glue a
        # credential into a longer token.  Shorter values are whole tokens
        # only.  Skipping them left a 7-character key in the internal-error
        # text (CX-15); an unbounded one-character replace would eat ordinary
        # words ("k" inside "unknown").  A token boundary does neither.
        if len(text_secret) >= 8:
            redacted = redacted.replace(text_secret, PLACEHOLDER)
        else:
            redacted = re.sub(
                r"(?<![A-Za-z0-9_\-])" + re.escape(text_secret)
                + r"(?![A-Za-z0-9_\-])",
                PLACEHOLDER, redacted)
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(PLACEHOLDER, redacted)
    redacted = _KEY_VALUE.sub(
        lambda match: match.group(1) + match.group(2) + PLACEHOLDER, redacted)
    return _LOCAL_PATH.sub(
        lambda match: _externalize(match.group(1) or match.group(2)), redacted)
