"""Local-only verification fixtures; never imported by production application code."""

from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape
from urllib.parse import unquote, urlsplit, parse_qs
import redis


@contextmanager
def object_server():
    class Objects(dict):
        modified = None
        delete_failures = None
        versioning = ""

    objects, mutex = Objects(), threading.Lock()
    objects.modified, objects.delete_failures = {}, {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, data=b"", mime="application/octet-stream"):
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Type", mime)
            self.end_headers()
            if data:
                self.wfile.write(data)

        def authorized(self):
            # A protocol fixture, not an AWS IAM emulator. SDK sends SigV4; this
            # fixture requires its presence and never accepts anonymous access.
            return self.headers.get("Authorization", "").startswith("AWS4-HMAC-SHA256 ")

        def do_PUT(self):
            if not self.authorized():
                self.respond(403)
                return
            path = unquote(self.path)
            size = int(self.headers.get("Content-Length", "0"))
            if (
                not path.startswith("/private-fixture/tenants/")
                or not 0 < size <= 10 * 1024 * 1024
            ):
                self.respond(400)
                return
            if (
                self.headers.get("If-None-Match") != "*"
                or self.headers.get("x-amz-server-side-encryption") != "AES256"
            ):
                self.respond(400)
                return
            data = self.rfile.read(size)
            if hashlib.sha256(data).hexdigest() != self.headers.get(
                "x-amz-meta-sha256"
            ):
                self.respond(400)
                return
            with mutex:
                if path in objects:
                    self.respond(
                        412,
                        b"<Error><Code>PreconditionFailed</Code></Error>",
                        "application/xml",
                    )
                    return
                objects[path] = (data, self.headers.get("Content-Type"))
                objects.modified[path] = datetime.now(timezone.utc).replace(
                    microsecond=0
                )
            self.respond(200)

        def do_GET(self):
            if not self.authorized():
                self.respond(403)
                return
            parsed = urlsplit(self.path)
            query = parse_qs(parsed.query)
            if parsed.path.rstrip("/") == "/private-fixture":
                if parsed.query == "versioning":
                    state = (
                        f"<Status>{objects.versioning}</Status>"
                        if objects.versioning
                        else ""
                    )
                    self.respond(
                        200,
                        f'<VersioningConfiguration xmlns="http://s3.amazonaws.com/doc/2006-03-01/">{state}</VersioningConfiguration>'.encode(),
                        "application/xml",
                    )
                    return
                if query.get("list-type") == ["2"]:
                    prefix = "/private-fixture/" + query.get("prefix", [""])[0]
                    after = query.get("continuation-token", [""])[0]
                    limit = int(query.get("max-keys", ["100"])[0])
                    with mutex:
                        keys = sorted(
                            k for k in objects if k.startswith(prefix) and k > after
                        )
                        xml = []
                        for path in keys[:limit]:
                            data = objects[path][0]
                            modified = objects.modified[path].isoformat()
                            etag = hashlib.md5(data, usedforsecurity=False).hexdigest()
                            xml.append(
                                f"<Contents><Key>{escape(path[len('/private-fixture/') :])}</Key><LastModified>{modified}</LastModified><ETag>&quot;{etag}&quot;</ETag><Size>{len(data)}</Size></Contents>"
                            )
                    more = len(keys) > limit
                    token = (
                        f"<NextContinuationToken>{escape(keys[limit - 1])}</NextContinuationToken>"
                        if more
                        else ""
                    )
                    self.respond(
                        200,
                        (
                            f'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>{str(more).lower()}</IsTruncated>{token}'
                            + "".join(xml)
                            + "</ListBucketResult>"
                        ).encode(),
                        "application/xml",
                    )
                    return
            with mutex:
                obj = objects.get(unquote(parsed.path))
            if obj is None:
                self.respond(
                    404, b"<Error><Code>NoSuchKey</Code></Error>", "application/xml"
                )
                return
            self.respond(200, *obj)

        def do_HEAD(self):
            if not self.authorized():
                self.respond(403)
                return
            path = unquote(urlsplit(self.path).path)
            with mutex:
                obj = objects.get(path)
                if obj is None:
                    self.respond(404)
                    return
                data, mime = obj
                modified = objects.modified[path]
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Type", mime)
            self.send_header(
                "ETag", '"' + hashlib.md5(data, usedforsecurity=False).hexdigest() + '"'
            )
            self.send_header("Last-Modified", format_datetime(modified, usegmt=True))
            self.end_headers()

        def do_DELETE(self):
            if not self.authorized():
                self.respond(403)
                return
            path = unquote(urlsplit(self.path).path)
            with mutex:
                if objects.delete_failures.get(path, 0):
                    objects.delete_failures[path] -= 1
                    self.respond(
                        503,
                        b"<Error><Code>ServiceUnavailable</Code></Error>",
                        "application/xml",
                    )
                    return
                obj = objects.get(path)
                if obj:
                    etag = (
                        '"'
                        + hashlib.md5(obj[0], usedforsecurity=False).hexdigest()
                        + '"'
                    )
                    if self.headers.get("If-Match") != etag:
                        self.respond(
                            412,
                            b"<Error><Code>PreconditionFailed</Code></Error>",
                            "application/xml",
                        )
                        return
                    del objects[path]
                    objects.modified.pop(path, None)
            self.respond(204)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", objects
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@contextmanager
def isolated_redis(executable=None):
    executable = executable or shutil.which("redis-server")
    if not executable or not Path(executable).is_file():
        raise RuntimeError(
            "Set REDIS_SERVER to an installed Redis binary for P6 verification"
        )
    with tempfile.TemporaryDirectory(prefix="inventory-p6-redis-") as directory:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        with Path(directory, "redis.log").open("w+") as log:
            process = subprocess.Popen(
                [
                    executable,
                    "--bind",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--save",
                    "",
                    "--appendonly",
                    "no",
                    "--dir",
                    directory,
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            try:
                client = redis.Redis(
                    host="127.0.0.1", port=port, socket_connect_timeout=1
                )
                deadline = time.monotonic() + 10
                while True:
                    try:
                        if client.ping():
                            break
                    except redis.RedisError:
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError(
                                "Disposable Redis failed to start"
                            ) from None
                        time.sleep(0.05)
                print(
                    "REDIS:",
                    client.info("server")["redis_version"],
                    "disposable loopback instance",
                    flush=True,
                )
                yield f"redis://127.0.0.1:{port}/0"
            finally:
                process.terminate()
                process.wait(timeout=10)
