"""Tests for the catalog compiler. Run: uv run --with pyyaml==6.0.2 python -m unittest discover -s scripts"""

import tempfile
import textwrap
import unittest
from pathlib import Path

import build


class CompileEntryTests(unittest.TestCase):
    def compile(self, filename: str, yaml_text: str):
        return build.compile_entry(Path(filename), build.yaml.safe_load(textwrap.dedent(yaml_text)))

    def assertInvalid(self, filename: str, yaml_text: str, message: str):
        with self.assertRaises(build.Invalid) as raised:
            self.compile(filename, yaml_text)
        self.assertIn(message, str(raised.exception))

    def test_single_feed_expands_to_every_architecture(self):
        bundle_id, entry = self.compile(
            "com.example.App.yml",
            """
            bundle_id: com.example.App
            name: Example
            sparkle_feed: https://example.com/appcast.xml
            """,
        )
        self.assertEqual(bundle_id, "com.example.App")
        self.assertEqual(
            entry["sparkleFeed"],
            {"arm64": "https://example.com/appcast.xml", "x86_64": "https://example.com/appcast.xml"},
        )

    def test_per_architecture_feeds(self):
        _, entry = self.compile(
            "com.example.App.yml",
            """
            bundle_id: com.example.App
            name: Example
            sparkle_feed:
              arm64: https://example.com/arm64.xml
            """,
        )
        self.assertEqual(entry["sparkleFeed"], {"arm64": "https://example.com/arm64.xml"})

    def test_fallback_feed_compiles_separately(self):
        _, entry = self.compile(
            "net.matthewpalmer.Rocket.yml",
            """
            bundle_id: net.matthewpalmer.Rocket
            name: Rocket
            fallback_sparkle_feed: https://macrelease.matthewpalmer.net/distribution/appcasts/rocket.xml
            """,
        )
        self.assertNotIn("sparkleFeed", entry)
        self.assertEqual(set(entry["fallbackSparkleFeed"]), {"arm64", "x86_64"})

    def test_feed_and_fallback_feed_are_exclusive(self):
        self.assertInvalid(
            "com.example.App.yml",
            """
            bundle_id: com.example.App
            name: Example
            sparkle_feed: https://example.com/a.xml
            fallback_sparkle_feed: https://example.com/b.xml
            """,
            "not both",
        )

    def test_notes_are_normalized(self):
        _, entry = self.compile(
            "com.example.App.yml",
            """
            bundle_id: com.example.App
            name: Example
            notes: >
              Updates itself
              in place.
            """,
        )
        self.assertEqual(entry["notes"], "Updates itself in place.")

    def test_rejects_unknown_keys(self):
        self.assertInvalid(
            "com.example.App.yml",
            "{bundle_id: com.example.App, name: X, sparkle_fed: 'https://example.com/a.xml'}",
            "unknown keys: sparkle_fed",
        )

    def test_filename_must_match_bundle_id(self):
        self.assertInvalid("other.yml", "{bundle_id: com.example.App, name: X, notes: hi}", "com.example.App.yml")

    def test_requires_a_directive(self):
        self.assertInvalid("com.example.App.yml", "{bundle_id: com.example.App, name: X}", "needs at least one of")

    def test_rejects_plain_http_feeds(self):
        self.assertInvalid(
            "com.example.App.yml",
            "{bundle_id: com.example.App, name: X, sparkle_feed: 'http://example.com/a.xml'}",
            "https://",
        )

    def test_rejects_unknown_architectures(self):
        self.assertInvalid(
            "com.example.App.yml",
            "{bundle_id: com.example.App, name: X, sparkle_feed: {ppc: 'https://example.com/a.xml'}}",
            "arm64 and/or x86_64",
        )

    def test_version_glob_rules(self):
        base = "{bundle_id: com.example.App, name: X, installed_version: {glob: '%s'}}"
        _, entry = self.compile("com.example.App.yml", base % "~/Library/Application Support/x/app-*.asar")
        self.assertEqual(entry["installedVersion"], {"glob": "~/Library/Application Support/x/app-*.asar"})
        self.assertInvalid("com.example.App.yml", base % "/etc/app-*.conf", "must start with")
        self.assertInvalid("com.example.App.yml", base % "~/Library/*/app-*.asar", "exactly one *")
        self.assertInvalid("com.example.App.yml", base % "~/Library/../app-*.asar", "exactly one *")
        self.assertInvalid("com.example.App.yml", base % "~/Library/x/app.asar", "exactly one *")

    def test_reports_every_problem_at_once(self):
        with self.assertRaises(build.Invalid) as raised:
            self.compile("x.yml", "{bundle_id: bad, homebrew_cask: 'Not A Token'}")
        message = str(raised.exception)
        for fragment in ("bundle_id", "name is required", "homebrew_cask"):
            self.assertIn(fragment, message)


class BuildTests(unittest.TestCase):
    def test_catalog_in_repo_is_valid(self):
        index, errors = build.build(build.ROOT / "apps")
        self.assertEqual(errors, [])
        self.assertEqual(index["schemaVersion"], 1)
        self.assertGreater(len(index["apps"]), 0)

    def test_case_insensitive_duplicates_collide(self):
        with tempfile.TemporaryDirectory() as directory:
            apps = Path(directory)
            (apps / "com.example.App.yml").write_text("bundle_id: com.example.App\nname: A\nnotes: a\n")
            (apps / "com.example.app.yml").write_text("bundle_id: com.example.app\nname: B\nnotes: b\n")
            (apps / "readme.txt").write_text("stray")
            # On a case-insensitive file system (macOS default) the second write replaced the first.
            case_sensitive = len(list(apps.glob("*.yml"))) == 2
            _, errors = build.build(apps)
        self.assertTrue(any("must end in .yml" in error for error in errors))
        self.assertEqual(any("same bundle ID" in error for error in errors), case_sensitive)


if __name__ == "__main__":
    unittest.main()
