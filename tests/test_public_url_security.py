import asyncio
import socket
import unittest
from unittest.mock import AsyncMock, patch

import main as agent_main
from public_url_security import (
    PublicEgressProxy,
    UnsafePublicURL,
    host_allowed_by_policy,
    is_public_ip_address,
    parse_public_web_url,
    resolve_public_host,
    validate_public_web_url,
)


def dns_record(address, port=443):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port, 0, 0) if family == socket.AF_INET6 else (address, port))


class PublicURLPolicyTests(unittest.TestCase):
    def test_public_giggleus_url_is_not_rejected_by_host_allowlist(self):
        url = "https://www.giggleus.com/ru/quizzes/science"
        target = parse_public_web_url(url, require_https=True)
        self.assertEqual(target.hostname, "www.giggleus.com")
        self.assertTrue(host_allowed_by_policy(target.hostname, "public_https", {
            "browser-agent-web-nine.vercel.app",
        }))

    def test_other_public_domains_are_allowed_without_configuration(self):
        for host in ("example.com", "quiz.example.org", "www.giggleus.com"):
            with self.subTest(host=host):
                target = parse_public_web_url(f"https://{host}/", require_https=True)
                self.assertTrue(host_allowed_by_policy(target.hostname, "public_https", set()))

    def test_strict_allowlist_is_explicit_and_exact(self):
        allowed = {"browser-agent-web-nine.vercel.app"}
        self.assertTrue(host_allowed_by_policy("browser-agent-web-nine.vercel.app", "strict_allowlist", allowed))
        self.assertFalse(host_allowed_by_policy("www.giggleus.com", "strict_allowlist", allowed))
        self.assertFalse(host_allowed_by_policy("www.browser-agent-web-nine.vercel.app", "strict_allowlist", allowed))

    def test_rejects_credentials_and_non_https_initial_urls(self):
        for url in (
            "https://user:password@example.com/",
            "https://user@example.com/",
            "http://example.com/",
            "file:///etc/passwd",
            "javascript:alert(1)",
        ):
            with self.subTest(url=url):
                with self.assertRaises(UnsafePublicURL):
                    parse_public_web_url(url, require_https=True)

    def test_rejects_local_reserved_and_nonstandard_port_targets(self):
        for url in (
            "https://localhost/",
            "https://foo.localhost/",
            "https://service.internal/",
            "https://printer.local/",
            "https://127.0.0.1/",
            "https://10.0.0.1/",
            "https://169.254.169.254/",
            "https://[::1]/",
            "https://192.168.1.10/",
            "https://example.com:8443/",
            "https://example.com:444/",
        ):
            with self.subTest(url=url):
                with self.assertRaises(UnsafePublicURL):
                    parse_public_web_url(url, require_https=True)

    def test_ip_global_classifier_rejects_common_private_ranges(self):
        for address in ("127.0.0.1", "10.1.2.3", "172.16.1.1", "192.168.1.1",
                        "169.254.169.254", "::1", "fe80::1", "fc00::1", "0.0.0.0"):
            with self.subTest(address=address):
                self.assertFalse(is_public_ip_address(address))
        self.assertTrue(is_public_ip_address("93.184.216.34"))

    def test_dns_answer_must_be_public(self):
        async def resolver(host, port):
            return [dns_record("93.184.216.34", port)]

        addresses = asyncio.run(resolve_public_host("www.giggleus.com", 443, resolver=resolver))
        self.assertEqual(addresses, ["93.184.216.34"])

    def test_dns_answer_with_any_private_address_is_rejected(self):
        async def resolver(host, port):
            return [dns_record("93.184.216.34", port), dns_record("127.0.0.1", port)]

        with self.assertRaises(UnsafePublicURL):
            asyncio.run(resolve_public_host("www.giggleus.com", 443, resolver=resolver))

    def test_dns_answer_metadata_and_link_local_are_rejected(self):
        async def resolver(host, port):
            return [dns_record("169.254.169.254", port)]

        with self.assertRaises(UnsafePublicURL):
            asyncio.run(resolve_public_host("www.giggleus.com", 443, resolver=resolver))

    def test_validate_public_url_uses_injected_dns_resolver(self):
        async def resolver(host, port):
            self.assertEqual(host, "www.giggleus.com")
            self.assertEqual(port, 443)
            return [dns_record("93.184.216.34", port)]

        target, addresses = asyncio.run(
            validate_public_web_url(
                "https://www.giggleus.com/ru/quizzes/science",
                require_https=True,
                resolver=resolver,
            )
        )
        self.assertEqual(target.path_and_query, "/ru/quizzes/science")
        self.assertEqual(addresses, ["93.184.216.34"])

    def test_proxy_rejects_loopback_connect_before_opening_upstream(self):
        async def scenario():
            proxy = await PublicEgressProxy().start()
            try:
                host, port = proxy.proxy_url.removeprefix("http://").split(":")
                reader, writer = await asyncio.open_connection(host, int(port))
                writer.write(
                    b"CONNECT 127.0.0.1:443 HTTP/1.1\r\n"
                    b"Host: 127.0.0.1:443\r\n\r\n"
                )
                await writer.drain()
                response = await asyncio.wait_for(reader.read(4096), timeout=2)
                writer.close()
                await writer.wait_closed()
                self.assertTrue(response.startswith(b"HTTP/1.1 403"), response[:200])
            finally:
                await proxy.close()

        asyncio.run(scenario())



class BrowserRequestGuardTests(unittest.TestCase):
    def test_unsafe_redirect_or_subresource_is_aborted(self):
        class Request:
            url = "http://127.0.0.1:8080/admin"

        class Route:
            request = Request()

            def __init__(self):
                self.aborted = False
                self.continued = False

            async def abort(self, reason):
                self.aborted = True

            async def continue_(self):
                self.continued = True

        async def scenario():
            route = Route()
            with patch(
                "main.validate_public_web_url",
                new=AsyncMock(side_effect=UnsafePublicURL("private address")),
            ):
                await agent_main.block_non_web_schemes(route)
            return route

        route = asyncio.run(scenario())
        self.assertTrue(route.aborted)
        self.assertFalse(route.continued)

    def test_public_https_subresource_is_continued(self):
        class Request:
            url = "https://cdn.example.org/static/app.js"

        class Route:
            request = Request()

            def __init__(self):
                self.aborted = False
                self.continued = False

            async def abort(self, reason):
                self.aborted = True

            async def continue_(self):
                self.continued = True

        async def scenario():
            route = Route()
            with patch(
                "main.validate_public_web_url",
                new=AsyncMock(return_value=(parse_public_web_url(Request.url), ["93.184.216.34"])),
            ):
                await agent_main.block_non_web_schemes(route)
            return route

        route = asyncio.run(scenario())
        self.assertFalse(route.aborted)
        self.assertTrue(route.continued)


if __name__ == "__main__":
    unittest.main()
