import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from sql2es.convert import SearchRequest
from sql2es.errors import Sql2EsError


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _scrub(msg, secrets):
    for s in secrets:
        if s:
            msg = msg.replace(s, "***")
    return msg


def search(base_url, req):
    try:
        parts = urllib.parse.urlsplit(base_url)
    except ValueError:
        raise Sql2EsError(_scrub("invalid base URL", [base_url]))

    secrets = []
    for s in (
        base_url,
        parts.netloc,
        parts.hostname,
        parts.username,
        parts.password,
        urllib.parse.unquote(parts.username) if parts.username is not None else None,
        urllib.parse.unquote(parts.password) if parts.password is not None else None,
    ):
        if s and s not in secrets:
            secrets.append(s)

    if parts.scheme not in ("http", "https"):
        raise Sql2EsError(_scrub("unsupported base URL scheme", secrets))
    if not parts.hostname:
        raise Sql2EsError(_scrub("invalid base URL", secrets))
    try:
        port = parts.port
    except ValueError:
        raise Sql2EsError(_scrub("invalid base URL", secrets))

    host = parts.hostname
    if ":" in host:
        host = f"[{host}]"
    if port is not None:
        host = f"{host}:{port}"
    netloc = host

    path = parts.path
    if path.endswith("/"):
        path = path[:-1]
    path += "/" + urllib.parse.quote(req.index, safe="!$&'()*+,;=:@") + "/_search"

    scheme = parts.scheme
    url = urllib.parse.urlunsplit((scheme, netloc, path, "", ""))
    secrets.append(url)

    headers = {
        "Content-Type": "application/json",
    }
    if parts.username is not None or parts.password is not None:
        creds = urllib.parse.unquote(parts.username or "") + ":" + urllib.parse.unquote(parts.password or "")
        headers["Authorization"] = "Basic " + base64.b64encode(creds.encode("utf-8")).decode("ascii")

    data = json.dumps(req.body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    request = urllib.request.Request(url, data=data, headers=headers, method="POST")

    def fail(msg):
        raise Sql2EsError(_scrub(msg, secrets))

    opener = urllib.request.build_opener(_NoRedirect)
    start = time.monotonic()
    try:
        with opener.open(request, timeout=30) as resp:
            status = resp.status
            buf = b""
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                if time.monotonic() - start > 30:
                    raise Sql2EsError(_scrub("request failed: timed out after 30s", secrets))
                buf += chunk
            text = buf.decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            text = e.read().decode("utf-8", "replace")
        except Exception:
            text = ""
        fail(f"HTTP {e.code} {e.reason}: {text}")
    except Sql2EsError:
        raise
    except Exception as e:
        fail(f"request failed: {getattr(e, 'reason', e)}")

    if 200 <= status < 300:
        return text
    # unreachable: _NoRedirect turns redirects into HTTPError
    fail(f"HTTP {status}")
