"""Tiny HTTP helper on the standard library: JSON, basic auth, cookies and optional no-redirect."""
from __future__ import annotations

import base64
import http.client
import json
import urllib.error
import urllib.request
from typing import Any


class HttpError(RuntimeError):
    def __init__(self, status: int, url: str, body: str):
        super().__init__(f"HTTP {status} from {url.split('?')[0]}: {body[:300]}")
        self.status = status
        self.body = body


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def opener(cookies: bool = False, follow: bool = True) -> urllib.request.OpenerDirector:
    handlers: list = []
    if cookies:
        import http.cookiejar
        handlers.append(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    if not follow:
        handlers.append(_NoRedirect())
    return urllib.request.build_opener(*handlers)


_DEFAULT = opener()


def request(method: str, url: str, *, json_body: Any = None, headers: dict | None = None,
            auth: tuple[str, str] | None = None, via: urllib.request.OpenerDirector | None = None,
            timeout: float = 10.0) -> tuple[int, dict, bytes]:
    hdrs = {"Accept": "application/json", "User-Agent": "agentops-lab", **(headers or {})}
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode()
        hdrs["Content-Type"] = "application/json"
    elif method in ("POST", "PUT"):
        data = b""
    if auth:
        hdrs["Authorization"] = "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with (via or _DEFAULT).open(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308):
            return e.code, dict(e.headers), b""
        raise HttpError(e.code, url, e.read().decode("utf-8", errors="replace")) from None
    except urllib.error.URLError as e:
        raise HttpError(0, url, f"cannot connect ({e.reason})") from None
    except (ConnectionError, TimeoutError, http.client.HTTPException) as e:  # dropped mid-request, e.g. a restart
        raise HttpError(0, url, f"connection lost ({type(e).__name__})") from None


def get_json(url: str, **kw) -> Any:
    _status, _headers, body = request("GET", url, **kw)
    return json.loads(body or b"null")
