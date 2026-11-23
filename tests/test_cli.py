import base64
import json
import os
import socket
import subprocess
import sys
import threading
from urllib.parse import urlsplit

from sql2es.convert import convert

SQL = "SELECT * FROM t"
DRYRUN_SQL = "SELECT a FROM t WHERE b = 1"
DRYRUN_BODY = {
    "_source": ["a"],
    "query": {"bool": {"filter": [{"term": {"b": {"value": 1}}}]}},
    "size": 10000,
}


def run(args, input=None):
    env = {**os.environ, "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"}
    kwargs = dict(
        capture_output=True,
        env=env,
        timeout=60,
    )
    if input is None:
        kwargs["stdin"] = subprocess.DEVNULL
    else:
        kwargs["input"] = input
    return subprocess.run(
        [sys.executable, "-m", "sql2es", *args],
        **kwargs,
    )


def spawn_server(code, reply_body, record_headers=False, allow_second_accept=False, second_accept_timeout=2.0, location=None):
    server = socket.create_server(("127.0.0.1", 0))
    server.settimeout(15)
    port = server.getsockname()[1]
    result = {"connections": 0}

    def serve():
        try:
            conn, _ = server.accept()
            result["connections"] += 1
            try:
                with conn.makefile("rb") as f:
                    request_line = f.readline().decode("utf-8", "replace").rstrip("\r\n")
                    content_length = 0
                    headers = {}
                    while True:
                        line = f.readline().decode("utf-8", "replace")
                        trimmed = line.rstrip("\r\n")
                        if trimmed == "":
                            break
                        if ":" in trimmed:
                            name, _, value = trimmed.partition(":")
                            headers[name.strip().lower()] = value.strip()
                            if name.strip().lower() == "content-length":
                                content_length = int(value.strip())
                    body = f.read(content_length).decode("utf-8", "replace")
                    if code == 200:
                        status_text = "OK"
                    elif code == 302:
                        status_text = "Found"
                    else:
                        status_text = "Bad Request"
                    payload = reply_body.encode("utf-8")
                    response = (
                        f"HTTP/1.1 {code} {status_text}\r\n"
                        f"Connection: close\r\n"
                        f"Content-Length: {len(payload)}\r\n"
                        f"Content-Type: application/json\r\n"
                    )
                    if location is not None:
                        response += f"Location: {location}\r\n"
                    response += "\r\n"
                    response = response.encode("utf-8") + payload
                    conn.sendall(response)
                    result["request_line"] = request_line
                    result["body"] = body
                    if record_headers:
                        result["authorization"] = headers.get("authorization")
                        result["content_type"] = headers.get("content-type")
            finally:
                conn.close()
            if allow_second_accept:
                server.settimeout(second_accept_timeout)
                try:
                    conn2, _ = server.accept()
                    result["connections"] += 1
                    conn2.close()
                except socket.timeout:
                    pass
        finally:
            server.close()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{port}", thread, result


def test_dryrun_without_url_exits_zero_and_prints_json():
    out = run(["--dryrun", DRYRUN_SQL])
    assert out.returncode == 0, f"stderr: {out.stderr.decode('utf-8', 'replace')}"
    parsed = json.loads(out.stdout)
    assert parsed == DRYRUN_BODY


def test_dryrun_with_equals_and_stdin():
    out = run(["--dryrun=true"], input=DRYRUN_SQL.encode())
    assert out.returncode == 0, f"stderr: {out.stderr.decode('utf-8', 'replace')}"
    parsed = json.loads(out.stdout)
    assert parsed == DRYRUN_BODY


def test_missing_url_and_dryrun_fails():
    out = run([SQL])
    assert out.returncode == 1
    assert out.stdout == b"", f"stdout: {out.stdout.decode('utf-8', 'replace')}"
    stderr = out.stderr.decode("utf-8", "replace")
    assert "--url" in stderr, f"stderr: {stderr}"


def test_live_request_is_sent_and_response_is_stdout():
    url, thread, result = spawn_server(200, '{"ok":true}')
    try:
        out = run(["--url", url, SQL])
        thread.join(20)
        assert out.returncode == 0, f"stderr: {out.stderr.decode('utf-8', 'replace')}"
        assert result["request_line"] == "POST /t/_search HTTP/1.1"
        expected = json.dumps(convert(SQL).body, separators=(",", ":"), ensure_ascii=False)
        assert result["body"] == expected
        assert out.stdout.rstrip() == b'{"ok":true}'
    finally:
        thread.join(5)


def test_live_request_with_400_reply_exits_nonzero():
    url, thread, result = spawn_server(400, '{"error":"boom"}')
    try:
        out = run(["--url", url, SQL])
        thread.join(20)
        assert result["request_line"] == "POST /t/_search HTTP/1.1"
        assert out.returncode == 1
        assert out.stdout == b"", f"stdout: {out.stdout.decode('utf-8', 'replace')}"
    finally:
        thread.join(5)


def test_credential_from_url_does_not_leak_into_stderr():
    out = run(["--url", "http://u:secret@127.0.0.1:1", SQL])
    assert out.returncode == 1
    stderr = out.stderr.decode("utf-8", "replace")
    assert "secret" not in stderr, f"stderr: {stderr}"


def test_bogus_option_exits_2_with_usage():
    out = run(["--bogus", "SELECT a FROM t"])
    assert out.returncode == 2
    stderr = out.stderr.decode("utf-8", "replace")
    assert "usage:" in stderr, f"stderr: {stderr}"


def test_help_exits_0_with_usage():
    out = run(["--help"])
    assert out.returncode == 0
    stdout = out.stdout.decode("utf-8", "replace")
    assert "usage: sql2es" in stdout, f"stdout: {stdout}"


def test_dryrun_false_without_url_exits_1_with_url_error():
    out = run(["--dryrun=false", "SELECT a FROM t"])
    assert out.returncode == 1
    stderr = out.stderr.decode("utf-8", "replace")
    assert "--url is required unless --dryrun is set" in stderr, f"stderr: {stderr}"


def test_invalid_dryrun_value_exits_2():
    out = run(["--dryrun=maybe", "SELECT a FROM t"])
    assert out.returncode == 2


def test_redirect_response_exits_nonzero_and_no_second_request():
    url, thread, result = spawn_server(
        302, "{}",
        allow_second_accept=True,
        second_accept_timeout=2.0,
        location="http://127.0.0.1:9/next",
    )
    try:
        out = run(["--url", url, SQL])
        thread.join(20)
        assert out.returncode != 0, f"stdout: {out.stdout!r} stderr: {out.stderr!r}"
        assert result.get("connections") == 1, f"connections: {result.get('connections')}"
    finally:
        thread.join(5)


def test_basic_auth_header_sent_for_credentials_in_url():
    url, thread, result = spawn_server(200, '{"ok":true}', record_headers=True)
    port = urlsplit(url).port
    try:
        out = run(["--url", f"http://u:pw@127.0.0.1:{port}", SQL])
        thread.join(20)
        assert out.returncode == 0, f"stderr: {out.stderr.decode('utf-8', 'replace')}"
        expected = "Basic " + base64.b64encode(b"u:pw").decode("ascii")
        assert result.get("authorization") == expected, f"authorization: {result.get('authorization')!r}"
    finally:
        thread.join(5)


def test_content_type_header_is_sent():
    url, thread, result = spawn_server(200, '{"ok":true}', record_headers=True)
    try:
        out = run(["--url", url, SQL])
        thread.join(20)
        assert out.returncode == 0, f"stderr: {out.stderr.decode('utf-8', 'replace')}"
        assert result.get("content_type") == "application/json", f"content_type: {result.get('content_type')!r}"
    finally:
        thread.join(5)


def test_password_not_leaked_on_http_500():
    url, thread, result = spawn_server(500, "boom")
    port = urlsplit(url).port
    try:
        out = run(["--url", f"http://u:secretpw@127.0.0.1:{port}", SQL])
        thread.join(20)
        assert out.returncode == 1
        stderr = out.stderr.decode("utf-8", "replace")
        assert "secretpw" not in stderr, f"stderr: {stderr}"
    finally:
        thread.join(5)
