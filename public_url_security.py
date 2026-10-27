"""Public HTTPS URL validation and DNS-pinning egress proxy for Playwright.

Every browser HTTP(S) request must use this proxy. It resolves each requested
hostname, rejects unsafe DNS answers, and opens the socket to the validated IP
directly so the destination cannot be re-resolved between validation and connect.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit


class UnsafePublicURL(ValueError):
    """Raised when a URL is malformed or does not identify a public web target."""


LOCAL_HOST_SUFFIXES = (
    ".localhost", ".local", ".internal", ".lan", ".home", ".test",
    ".invalid", ".example",
)
BLOCKED_HOSTNAMES = {
    "localhost", "metadata", "metadata.google.internal",
    "metadata.google", "instance-data", "instance-data.ec2.internal",
}
ALLOWED_SCHEMES = {"http", "https"}


@dataclass(frozen=True)
class PublicURL:
    original: str
    scheme: str
    hostname: str
    port: int
    path_and_query: str


def is_public_ip_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    return bool(
        address.is_global
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_multicast
        and not address.is_reserved
        and not address.is_unspecified
    )


def _normalize_hostname(hostname: str) -> str:
    host = hostname.strip().rstrip(".").lower()
    if not host or "%" in host or "\\" in host or any(ch.isspace() for ch in host):
        raise UnsafePublicURL("A valid public hostname is required.")

    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not is_public_ip_address(host):
            raise UnsafePublicURL("Non-public IP addresses are not allowed.")
        return host

    try:
        host = host.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError) as exc:
        raise UnsafePublicURL("The hostname is invalid.") from exc

    if host in BLOCKED_HOSTNAMES or any(host.endswith(suffix) for suffix in LOCAL_HOST_SUFFIXES):
        raise UnsafePublicURL("Local and metadata hostnames are not allowed.")
    if "." not in host:
        raise UnsafePublicURL("Single-label and intranet hostnames are not allowed.")

    labels = host.split(".")
    if len(host) > 253 or any(
        not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
        or not all(ch.isalnum() or ch == "-" for ch in label)
        for label in labels
    ):
        raise UnsafePublicURL("The hostname is invalid.")
    return host


def parse_public_web_url(raw_url: str, *, require_https: bool = False) -> PublicURL:
    """Validate URL structure and allowed web ports before resolving DNS."""
    if not isinstance(raw_url, str) or not raw_url or raw_url != raw_url.strip():
        raise UnsafePublicURL("Enter a valid absolute HTTPS URL.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw_url) or "\\" in raw_url:
        raise UnsafePublicURL("The URL contains invalid characters.")

    try:
        parsed = urlsplit(raw_url)
        hostname_raw = parsed.hostname
        explicit_port = parsed.port
    except ValueError as exc:
        raise UnsafePublicURL("The URL is malformed.") from exc

    scheme = parsed.scheme.lower()
    if require_https and scheme != "https":
        raise UnsafePublicURL("The target must use HTTPS.")
    if not require_https and scheme not in ALLOWED_SCHEMES:
        raise UnsafePublicURL("Only HTTP and HTTPS web requests are allowed.")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafePublicURL("URLs containing embedded credentials are not allowed.")
    if not hostname_raw:
        raise UnsafePublicURL("A hostname is required.")

    hostname = _normalize_hostname(hostname_raw)
    port = explicit_port if explicit_port is not None else (443 if scheme == "https" else 80)
    permitted_ports = {443} if (require_https or scheme == "https") else {80}
    if port not in permitted_ports:
        raise UnsafePublicURL("Only HTTPS port 443 and HTTP resource port 80 are allowed.")

    path_and_query = parsed.path or "/"
    if parsed.query:
        path_and_query += "?" + parsed.query
    return PublicURL(raw_url, scheme, hostname, port, path_and_query)


async def resolve_public_host(hostname: str, port: int, *, resolver=None,
                              timeout_seconds: float = 5.0) -> list[str]:
    """Resolve once, rejecting a hostname if any answer is non-public."""
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        if not is_public_ip_address(str(literal)):
            raise UnsafePublicURL("Non-public IP addresses are not allowed.")
        return [str(literal)]

    host = _normalize_hostname(hostname)
    if resolver is None:
        loop = asyncio.get_running_loop()

        async def resolver(name: str, port_number: int):
            return await loop.getaddrinfo(name, port_number, type=socket.SOCK_STREAM)

    try:
        records = await asyncio.wait_for(resolver(host, port), timeout=timeout_seconds)
    except (asyncio.TimeoutError, OSError, socket.gaierror) as exc:
        raise UnsafePublicURL("The public hostname could not be resolved.") from exc
    except Exception as exc:
        raise UnsafePublicURL("The public hostname could not be resolved safely.") from exc

    addresses: list[str] = []
    for record in records:
        try:
            address = str(record[4][0]).split("%", 1)[0]
            parsed_address = ipaddress.ip_address(address)
        except (IndexError, TypeError, ValueError) as exc:
            raise UnsafePublicURL("DNS returned an invalid address.") from exc
        if not is_public_ip_address(str(parsed_address)):
            raise UnsafePublicURL("DNS resolved to a private or otherwise unsafe address.")
        rendered = str(parsed_address)
        if rendered not in addresses:
            addresses.append(rendered)
    if not addresses:
        raise UnsafePublicURL("The public hostname has no usable IP addresses.")
    return addresses


async def validate_public_web_url(raw_url: str, *, require_https: bool = False,
                                  resolver=None) -> tuple[PublicURL, list[str]]:
    target = parse_public_web_url(raw_url, require_https=require_https)
    addresses = await resolve_public_host(target.hostname, target.port, resolver=resolver)
    return target, addresses


def host_allowed_by_policy(hostname: str, policy_mode: str,
                           allowed_hosts: set[str]) -> bool:
    """Support public_https by default or explicit strict_allowlist deployments."""
    mode = (policy_mode or "").strip().lower()
    if mode == "public_https":
        return True
    if mode == "strict_allowlist":
        normalized = hostname.lower().rstrip(".")
        return normalized in {host.lower().rstrip(".") for host in allowed_hosts}
    return False


class PublicEgressProxy:
    """HTTP CONNECT proxy that pins each outbound request to a validated IP."""

    def __init__(self, *, request_timeout_seconds: float = 10.0):
        self.request_timeout_seconds = request_timeout_seconds
        self._server: asyncio.AbstractServer | None = None
        self.proxy_url: str | None = None

    async def start(self) -> "PublicEgressProxy":
        self._server = await asyncio.start_server(
            self._handle_client, host="127.0.0.1", port=0, limit=65536,
        )
        port = self._server.sockets[0].getsockname()[1]
        self.proxy_url = f"http://127.0.0.1:{port}"
        return self

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def __aenter__(self) -> "PublicEgressProxy":
        return await self.start()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    @staticmethod
    async def _respond(writer: asyncio.StreamWriter, status: int, reason: str) -> None:
        body = (reason + "\n").encode("utf-8")
        writer.write(
            f"HTTP/1.1 {status} {reason}\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n".encode("ascii") + body
        )
        try:
            await writer.drain()
        except (ConnectionError, OSError):
            pass

    @staticmethod
    async def _read_headers(reader: asyncio.StreamReader) -> list[bytes]:
        headers: list[bytes] = []
        total = 0
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=10.0)
            total += len(line)
            if not line or total > 65536:
                raise UnsafePublicURL("Invalid or oversized proxy headers.")
            if line in (b"\r\n", b"\n"):
                break
            headers.append(line)
        return headers

    @staticmethod
    async def _connect_pinned(addresses: list[str], port: int):
        last_error: Exception | None = None
        for address in addresses:
            try:
                family = socket.AF_INET6 if ":" in address else socket.AF_INET
                return await asyncio.wait_for(
                    asyncio.open_connection(address, port, family=family), timeout=8.0,
                )
            except (OSError, asyncio.TimeoutError) as exc:
                last_error = exc
        raise OSError("Could not connect to a validated public IP address.") from last_error

    async def _tunnel(self, client_reader, client_writer, upstream_reader, upstream_writer) -> None:
        async def relay(source, destination):
            while True:
                data = await source.read(65536)
                if not data:
                    return
                destination.write(data)
                await destination.drain()

        tasks = [
            asyncio.create_task(relay(client_reader, upstream_writer)),
            asyncio.create_task(relay(upstream_reader, client_writer)),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _handle_client(self, client_reader: asyncio.StreamReader,
                             client_writer: asyncio.StreamWriter) -> None:
        upstream_writer = None
        try:
            request_line = await asyncio.wait_for(
                client_reader.readline(), timeout=self.request_timeout_seconds,
            )
            if not request_line or len(request_line) > 8192:
                await self._respond(client_writer, 400, "Invalid request")
                return
            try:
                method, target_raw, _version = request_line.decode("latin-1").strip().split(" ", 2)
            except ValueError:
                await self._respond(client_writer, 400, "Invalid request")
                return
            headers = await self._read_headers(client_reader)

            if method.upper() == "CONNECT":
                target = parse_public_web_url(f"https://{target_raw}/", require_https=True)
                addresses = await resolve_public_host(target.hostname, target.port)
                upstream_reader, upstream_writer = await self._connect_pinned(addresses, target.port)
                client_writer.write(b"HTTP/1.1 200 Connection Established\r\nProxy-Agent: BrowserAgent\r\n\r\n")
                await client_writer.drain()
                await self._tunnel(client_reader, client_writer, upstream_reader, upstream_writer)
                return

            target = parse_public_web_url(target_raw)
            if target.scheme != "http" or method.upper() not in {"GET", "HEAD"}:
                await self._respond(client_writer, 403, "Only public HTTP GET/HEAD resources are allowed")
                return
            addresses = await resolve_public_host(target.hostname, target.port)
            upstream_reader, upstream_writer = await self._connect_pinned(addresses, target.port)

            forwarded_headers: list[bytes] = []
            saw_host = False
            forbidden_headers = {
                b"proxy-connection", b"proxy-authorization", b"connection",
                b"keep-alive", b"transfer-encoding",
            }
            for header in headers:
                name = header.split(b":", 1)[0].strip().lower()
                if name == b"host":
                    saw_host = True
                    forwarded_headers.append(f"Host: {target.hostname}\r\n".encode("ascii"))
                elif name not in forbidden_headers and b":" in header:
                    forwarded_headers.append(header)
            if not saw_host:
                forwarded_headers.append(f"Host: {target.hostname}\r\n".encode("ascii"))
            forwarded_headers.append(b"Connection: close\r\n")
            upstream_writer.write(
                f"{method.upper()} {target.path_and_query} HTTP/1.1\r\n".encode("ascii")
                + b"".join(forwarded_headers) + b"\r\n"
            )
            await upstream_writer.drain()
            while True:
                chunk = await upstream_reader.read(65536)
                if not chunk:
                    break
                client_writer.write(chunk)
                await client_writer.drain()

        except UnsafePublicURL as exc:
            await self._respond(client_writer, 403, str(exc))
        except (asyncio.TimeoutError, OSError, ConnectionError):
            try:
                await self._respond(client_writer, 502, "Public destination unavailable")
            except Exception:
                pass
        except Exception:
            try:
                await self._respond(client_writer, 400, "Request rejected")
            except Exception:
                pass
        finally:
            if upstream_writer is not None:
                upstream_writer.close()
                try:
                    await upstream_writer.wait_closed()
                except (ConnectionError, OSError):
                    pass
            client_writer.close()
            try:
                await client_writer.wait_closed()
            except (ConnectionError, OSError):
                pass
