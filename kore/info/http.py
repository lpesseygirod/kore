"""One plain GET for the outside information sources (weather, news). Standard library only.

Never raises and never puts the URL in an error. Responses are size-limited: a feed that
suddenly sends 50 MB is refused, not read.
"""

from __future__ import annotations

import urllib.error
import urllib.request

TIMEOUT_S = 10
MAX_BYTES = 2_000_000
USER_AGENT = "KORE/0.1 (home companion; personal use)"


class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    """Follow redirects, but never from https down to plain http."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if req.full_url.startswith("https://") and not newurl.startswith("https://"):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_HttpsOnly)


def get(url: str, timeout: float = TIMEOUT_S, max_bytes: int = MAX_BYTES) -> tuple[int, bytes | None]:
    """(HTTP status, body). 0 means no connection, a bad URL, or a body over max_bytes.
    `timeout` is per network read: callers in a turn use kore.info.fetch for a total limit."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with _opener.open(req, timeout=timeout) as r:
            body = r.read(max_bytes + 1)
            if len(body) > max_bytes:
                return 0, None
            return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:  # no connection, a timeout, a malformed URL: no details
        return 0, None
