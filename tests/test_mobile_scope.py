"""Mobile policy tests; synthetic upstreams only, never live Hermes state."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fake_dashboard import FakeHermes, TOKEN
from hermes_bridge.app import Bridge
from hermes_bridge.config import Config, ConfigError, load
from hermes_bridge.upstream import UpstreamResponse


class RecordingDashboard:
    def __init__(self):
        self.fake = FakeHermes()
        self.calls = []

    def request(self, method, path, **kw):
        self.calls.append((method, path, kw))
        status, body = self.fake.route(method, path, kw.get("query", {}), kw.get("body"), TOKEN)
        return UpstreamResponse(status, body)


class RecordingGateway:
    def __init__(self):
        self.calls = []

    def call(self, profile, method, params):
        self.calls.append((profile, method, params))
        return {"type": "exec", "output": "synthetic reply"}


class MobileScopeTest(unittest.TestCase):
    def config(self, **extra):
        return load(environ={"HERMES_BRIDGE_KEY_FILE": "/not-read/key",
                             "HERMES_BRIDGE_DASHBOARD_TOKEN_FILE": "/not-read/token",
                             "HERMES_BRIDGE_ALLOWED_PROFILES": "default",
                             "HERMES_BRIDGE_NEW_PROFILE_PREFIX": "mobile-", **extra})

    def setUp(self):
        self.dash, self.gateway = RecordingDashboard(), RecordingGateway()
        self.bridge = Bridge(self.config(), self.dash, self.gateway)
        auth = patch.object(Config, "bridge_key", return_value="synthetic-test-key")
        auth.start()
        self.addCleanup(auth.stop)

    def req(self, method, path, body=None, query=None):
        return self.bridge.handle(method, path, query or {},
                                  {"authorization": "Bearer synthetic-test-key"},
                                  b"" if body is None else json.dumps(body).encode())

    def test_denies_every_out_of_scope_profile_route_before_upstream(self):
        # Derive every registered profile route, so future endpoints inherit coverage.
        for method, pattern, _, _, _ in self.bridge.routes:
            if "(?P<p>" not in pattern.pattern:
                continue
            import re
            path = re.sub(r"\(\?P<action>[^)]+\)", lambda m: "pause" if "pause" in m[0] else "enable", pattern.pattern)
            path = re.sub(r"\(\?P<\w+>[^)]+\)", "scout", path).strip("^$")
            for profile in ("scout", "%73cout", "other", "mobile"):
                with self.subTest(method=method, path=path, profile=profile):
                    status, result = self.req(method, path.replace("/profiles/scout/", f"/profiles/{profile}/"), {})
                    self.assertEqual((status, result["error"]["code"]), (403, "profile_denied"))
                    self.assertEqual(self.dash.calls, [])
                    self.assertEqual(self.gateway.calls, [])

    def test_roster_filters_before_reading_key_flags(self):
        self.dash.fake.profiles["mobile-research"] = {"display_name": None}
        self.dash.fake.env["mobile-research"] = {}
        for invalid in ("mobile-", "mobile-invalid/path", "mobile-%2e%2e", "mobile-line\n"):
            self.dash.fake.profiles[invalid] = {"display_name": None}
            self.dash.fake.env[invalid] = {}
        status, body = self.req("GET", "/v1/profiles")
        self.assertEqual(status, 200)
        self.assertEqual([p["id"] for p in body["profiles"]], ["default", "mobile-research"])
        self.assertFalse(any(c[1] == "/api/env" and c[2].get("query", {}).get("profile") == "scout" for c in self.dash.calls))

    def test_creation_requires_new_namespace_before_upstream(self):
        for name in ("scout", "research", "default", "mobile-", "mobileish-test", "mobile-%73cout"):
            with self.subTest(name=name):
                self.assertIn(self.req("POST", "/v1/profiles", {"name": name})[0], (400, 403))
                self.assertEqual(self.dash.calls, [])
        status, body = self.req("POST", "/v1/profiles", {"name": "mobile-research"})
        self.assertEqual(status, 201, body)
        self.assertIn("mobile-research", self.dash.fake.profiles)
        self.assertEqual(self.req("POST", "/v1/profiles/mobile-research/key")[0], 200)

    def test_clone_from_is_forbidden_even_for_allowed_sources(self):
        for source in ("scout", "default", "mobile-source", "", None, 123):
            with self.subTest(source=source):
                self.assertEqual(self.req("POST", "/v1/profiles", {"name": "mobile-new", "clone_from": source})[0], 403)
                self.assertEqual(self.dash.calls, [])
                self.assertEqual(self.gateway.calls, [])

    def test_restart_is_denied_before_compatibility_probe(self):
        self.assertEqual(self.req("POST", "/v1/gateway/restart")[0], 403)
        self.assertEqual(self.dash.calls, [])
        self.assertEqual(self.gateway.calls, [])

    def test_commands_fail_closed_without_calling_gateway(self):
        commands = ("/yolo", "/yolo on", "/YOLO", "/yolo@bot", "/restart", "/unknown",
                    "/status\n/yolo", "/status\r/yolo", "/status\t/yolo", "/status /yolo",
                    "/yo\nlo", "/yo\tlo", "/status; /yolo", "/status&&/yolo", "/status|/yolo",
                    "/status\u2028/yolo", "/status\u0085/yolo", "/%79olo", "/status%0a/yolo",
                    "/title hello\n/yolo", "/title hello /yolo")
        for command in commands:
            with self.subTest(command=command):
                status, _ = self.req("POST", "/v1/profiles/default/commands/run", {"session_id": "local-123", "command": command})
                self.assertIn(status, (400, 403))
                self.assertEqual(self.gateway.calls, [])
                self.assertEqual(self.dash.calls, [])
        for command in ("/status", "/help", "/title Phone chat"):
            self.assertEqual(self.req("POST", "/v1/profiles/default/commands/run", {"session_id": "local-123", "command": command})[0], 200)

    def test_invalid_scope_configuration_fails_closed(self):
        for prefix in ("", "m", "default", "../", "Mobile-", "a" * 64 + "-", 123):
            with self.subTest(prefix=prefix):
                with self.assertRaises(ConfigError):
                    self.config(HERMES_BRIDGE_NEW_PROFILE_PREFIX=prefix)
        for profiles in ("default,../other", "Default", "default,all", "default,scout\n"):
            with self.subTest(profiles=profiles):
                with self.assertRaises(ConfigError):
                    self.config(HERMES_BRIDGE_ALLOWED_PROFILES=profiles)
        cfg = load(environ={"HERMES_BRIDGE_KEY_FILE": "/x", "HERMES_BRIDGE_DASHBOARD_TOKEN_FILE": "/y",
                            "HERMES_BRIDGE_ALLOWED_PROFILES": ""})
        self.assertTrue(cfg.mobile_scope)
        self.assertEqual(cfg.allowed_profiles, ())

    def test_query_or_body_cannot_override_profile_scope(self):
        for method, path, body, query in (
            ("GET", "/v1/profiles/default/jobs", None, {"profile": "scout"}),
            ("GET", "/v1/profiles/default/commands", None, {"profile": "all"}),
            ("PUT", "/v1/profiles/default/skills/enabled", {"name": "web", "enabled": False, "profile": "scout"}, {}),
            ("POST", "/v1/profiles", {"name": "mobile-new", "profile": "scout"}, {}),
            ("GET", "/v1/profiles", None, {"profile": "scout"}),
            ("PUT", "/v1/profiles/default/soul", {"content": "hi", "clone_from": "scout"}, {}),
        ):
            with self.subTest(method=method, path=path, body=body, query=query):
                self.assertEqual(self.req(method, path, body, query)[0], 403)
                self.assertEqual(self.dash.calls, [])
                self.assertEqual(self.gateway.calls, [])

    def test_command_catalog_exposes_only_mobile_allowlist(self):
        catalog = {"categories": [{"name": "Commands", "pairs": [["/status", "Status"], ["/yolo", "Unsafe"], ["/restart", "Restart"], ["/title", "Title"], ["/help", "Help"]]}],
                   "canon": {"/unsafe-alias": "/status", "/s": "/status"}, "plugin_commands": {"/deploy": "danger"}}
        with patch.object(self.gateway, "call", return_value=catalog):
            status, body = self.req("GET", "/v1/profiles/default/commands")
        self.assertEqual(status, 200)
        self.assertEqual([c["name"] for c in body["commands"]], ["status", "title", "help"])
        self.assertTrue(all(c["aliases"] == [] for c in body["commands"]))
        self.assertEqual(body["plugin_commands"], [])

    def wire_requests(self, requests):
        import threading
        import urllib.error
        import urllib.request
        from http.server import ThreadingHTTPServer
        from hermes_bridge.server import make_handler
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.bridge))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for path, expected in requests:
                with self.subTest(path=path):
                    request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}" + path,
                                                     headers={"Authorization": "Bearer synthetic-test-key"})
                    try:
                        response = urllib.request.urlopen(request)
                    except urllib.error.HTTPError as error:
                        response = error
                    with response:
                        self.assertEqual(response.status, expected)
                        response.read()
                    self.assertEqual(self.dash.calls, [])
                    self.assertEqual(self.gateway.calls, [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_http_rejects_ambiguous_or_blank_scope_query(self):
        self.wire_requests([
            ("/v1/profiles/default/jobs?profile=scout&profile=default", 400),
            ("/v1/profiles/default/jobs?profile=default&%70rofile=scout", 400),
            ("/v1/profiles/default/jobs?profile=", 403),
        ])

    def test_encoded_paths_and_queries_never_reach_upstream(self):
        from dataclasses import replace
        self.bridge.cfg = replace(self.bridge.cfg, base_path="/bridge")
        self.wire_requests([
            ("/bridge/v1/profiles/%73cout/jobs", 403),
            ("/v1/profiles/scout/jobs?profile=default", 403),
            ("/v1/profiles/%2573cout/jobs", 400),
            ("/v1/profiles/default%2f..%2fscout/jobs", 400),
            ("/v1/profiles/default%0a/jobs", 400),
            ("/v1/profiles/DEFAULT/jobs", 400),
            ("/v1/profiles/all/jobs", 403),
            ("/v1/profiles/mobile-/jobs", 403),
            ("/v1/profiles/default/jobs?%70rofile=%73cout", 403),
            ("/v1/profiles/default/jobs?profile=%2573cout", 403),
            ("/v1/profiles/default/commands?profile=all", 403),
            ("/v1/profiles?clone_from=default", 403),
        ])

    def test_allowlist_only_scope_disables_creation(self):
        from dataclasses import replace
        self.bridge.cfg = replace(self.bridge.cfg, new_profile_prefix=None)
        self.assertEqual(self.req("POST", "/v1/profiles", {"name": "mobile-new"})[0], 403)
        self.assertEqual(self.req("GET", "/v1/profiles/mobile-existing/jobs")[0], 403)
        self.assertEqual(self.dash.calls, [])
        self.assertEqual(self.gateway.calls, [])

    def test_unscoped_clone_payload_is_preserved(self):
        from dataclasses import replace
        self.bridge.cfg = replace(self.bridge.cfg, allowed_profiles=None, new_profile_prefix=None)
        self.assertEqual(self.req("POST", "/v1/profiles", {"name": "research", "clone_from": "scout"})[0], 201)
        created = [c for c in self.dash.calls if c[:2] == ("POST", "/api/profiles")]
        self.assertEqual(created[0][2]["body"]["clone_from"], "scout")
        self.assertEqual(self.req("POST", "/v1/profiles/default/commands/run", {"session_id": "local-123", "command": "/yolo"})[0], 200)

    def test_example_configuration_enables_default_and_mobile_namespace(self):
        example = Path(__file__).resolve().parents[1] / "examples" / "hermes-bridge.toml"
        cfg = load(str(example), environ={})
        self.assertEqual(cfg.allowed_profiles, ("default",))
        self.assertEqual(cfg.new_profile_prefix, "mobile-")
        overridden = load(str(example), environ={"HERMES_BRIDGE_ALLOWED_PROFILES": "mobile-existing",
                                                "HERMES_BRIDGE_NEW_PROFILE_PREFIX": "phone-"})
        self.assertEqual(overridden.allowed_profiles, ("mobile-existing",))
        self.assertEqual(overridden.new_profile_prefix, "phone-")

    def test_loads_mobile_scope_from_environment(self):
        cfg = self.config()
        self.assertEqual(getattr(cfg, "allowed_profiles", None), ("default",))
        self.assertEqual(getattr(cfg, "new_profile_prefix", None), "mobile-")
        self.assertTrue(cfg.mobile_scope)
        unscoped = load(environ={"HERMES_BRIDGE_KEY_FILE": "/x", "HERMES_BRIDGE_DASHBOARD_TOKEN_FILE": "/y"})
        self.assertFalse(unscoped.mobile_scope)
