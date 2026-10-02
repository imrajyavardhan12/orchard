"""Tests for the livecheck importer. Run: uv run --with pyyaml==6.0.2 python -m unittest discover -s scripts"""

import textwrap
import unittest
from datetime import date
from pathlib import Path

import build
import import_livecheck as il


def cask(token="rocket", version="1.9.5,88", app="Rocket.app", quit=None, prefs=(), saved=(), **extra):
    artifacts = [{"app": [app] if isinstance(app, str) else app}]
    if quit is not None:
        artifacts.append({"uninstall": [{"quit": quit}]})
    trash = [f"~/Library/Preferences/{value}.plist" for value in prefs]
    trash += [f"~/Library/Saved Application State/{value}.savedState" for value in saved]
    if trash:
        artifacts.append({"zap": [{"trash": trash}]})
    return {"token": token, "version": version, "artifacts": artifacts, **extra}


class CaskSourceTests(unittest.TestCase):
    def source(self, livecheck: str, extra: str = "") -> str:
        body = textwrap.indent(livecheck, "    ")
        return f'cask "x" do\n  version "1.0"\n{extra}\n  livecheck do\n{body}\n  end\nend\n'

    def test_literal_sparkle_feed(self):
        text = self.source('url "https://example.com/appcast.xml"\nstrategy :sparkle')
        self.assertEqual(il.parse_cask_source("x", text), {"x": "https://example.com/appcast.xml"})

    def test_custom_block_still_counts(self):
        text = self.source('url "https://example.com/a.xml"\nstrategy :sparkle do |item|\n  item.short_version\nend')
        self.assertEqual(il.parse_cask_source("x", text), {"x": "https://example.com/a.xml"})

    def test_interpolated_or_per_cpu_feeds_are_unusable(self):
        # Spark: "https://downloads.sparkmailapp.com/Spark#{version.major}/mac/dist/appcast.xml"
        interpolated = self.source('url "https://example.com/Spark#{version.major}/appcast.xml"\nstrategy :sparkle')
        self.assertEqual(il.parse_cask_source("x", interpolated), {"x": None})
        per_cpu = self.source('url "https://example.com/a.xml"\nstrategy :sparkle', extra="  on_arm do\n  end")
        self.assertEqual(il.parse_cask_source("x", per_cpu), {"x": None})

    def test_other_strategies_are_ignored(self):
        self.assertEqual(il.parse_cask_source("x", self.source('url :homepage\nregex(/v(\\d+)/)')), {})


class BundleIDTests(unittest.TestCase):
    def test_single_quit_id(self):
        # Rocket (homebrew-cask, 2026-10-02)
        self.assertEqual(il.bundle_id(cask(quit="net.matthewpalmer.Rocket")), "net.matthewpalmer.Rocket")

    def test_helpers_are_narrowed_by_preferences(self):
        # Ditto quits its audio driver too; only the app has a preferences file.
        ditto = cask(quit=["com.squirrels.Ditto", "com.squirrels.SquirrelsLoopbackAudioDriver"], prefs=["com.squirrels.Ditto"])
        self.assertEqual(il.bundle_id(ditto), "com.squirrels.Ditto")

    def test_several_quit_ids_without_a_tiebreak_are_ambiguous(self):
        # ForkLift quits and zaps both the app and its mini window.
        ids = ["com.binarynights.ForkLift-4", "com.binarynights.ForkLiftMini"]
        self.assertIsNone(il.bundle_id(cask(quit=ids, prefs=ids)))

    def test_single_preferences_file(self):
        self.assertEqual(il.bundle_id(cask(prefs=["com.Kobot.Adze"])), "com.Kobot.Adze")

    def test_saved_state_breaks_a_preferences_tie(self):
        divvy = cask(prefs=["com.mizage.direct.Divvy", "com.mizage.Divvy"], saved=["com.mizage.Divvy"])
        self.assertEqual(il.bundle_id(divvy), "com.mizage.Divvy")

    def test_apple_ids_and_wildcards_are_ignored(self):
        self.assertIsNone(il.bundle_id(cask(quit=["com.apple.Safari", "com.elgato.WaveLink*"])))
        self.assertIsNone(il.bundle_id(cask()))


class PlausibleTests(unittest.TestCase):
    def test_rejects_ids_that_are_not_the_app(self):
        # Seen in the first import run (2026-10-02).
        self.assertFalse(il.plausible("5YKZ4Y3DAW.group.com.intii.CopilotForXcode", "Copilot for Xcode"))
        self.assertFalse(il.plausible("86Z3GCJ4MF.com.noodlesoft.HazelHelper", "Hazel"))
        self.assertFalse(il.plausible("Herd.app", "Herd"))
        self.assertFalse(il.plausible("com.hezongyidev.BobHelper", "Bob"))

    def test_keeps_unusual_but_real_ids(self):
        self.assertTrue(il.plausible("abnerworks.Typora", "Typora"))
        self.assertTrue(il.plausible("io.wwdc.app", "WWDC"))
        self.assertTrue(il.plausible("com.toontownrewritten.Toontown-Launcher", "Toontown Launcher"))


class AppNameTests(unittest.TestCase):
    def test_plain_and_renamed_apps(self):
        self.assertEqual(il.app_name(cask()), "Rocket")
        self.assertEqual(il.app_name(cask(app=["Foo 2.app", {"target": "Foo.app"}])), "Foo")
        self.assertEqual(il.app_name(cask(app="Vendor/Tool.app")), "Tool")

    def test_needs_exactly_one_app(self):
        two = cask()
        two["artifacts"].append({"app": ["Other.app"]})
        self.assertIsNone(il.app_name(two))


class CandidateTests(unittest.TestCase):
    def test_filters_and_dedupes(self):
        casks = [
            cask("rocket", quit="net.matthewpalmer.Rocket"),
            cask("rocket@beta", quit="net.matthewpalmer.Rocket.beta"),
            cask("old", quit="com.example.Old", disabled=True),
            cask("twin-a", app="A.app", quit="com.example.Twin"),
            cask("twin-b", app="B.app", quit="com.example.Twin"),
            cask("brave-browser", app="Brave Browser.app", quit="com.brave.Browser"),
            cask("plain", quit="com.example.Plain"),
        ]
        feeds = {token["token"]: "https://example.com/" + token["token"] + ".xml" for token in casks}
        feeds["plain"] = "http://example.com/plain.xml"
        found, skipped = il.candidates(casks, feeds, existing={"com.brave.browser"})
        self.assertEqual([candidate.token for candidate in found], ["rocket"])
        self.assertEqual(skipped["rocket@beta"], "not the stable channel")
        self.assertEqual(skipped["old"], "disabled or deprecated")
        self.assertEqual(skipped["twin-a"], "another cask claims the same bundle ID")
        self.assertEqual(skipped["brave-browser"], "hand-written entry exists")
        self.assertEqual(skipped["plain"], "feed is not https")


class MatchTests(unittest.TestCase):
    def test_real_feeds(self):
        # What `ripe feed` returned on 2026-10-02, against the cask's version.
        accepted = [
            ("1.9.5,88", il.FeedResult("1.9.5", "88", None)),  # Rocket
            ("3.29.2", il.FeedResult("3.29.2(400)", "400", None)),  # Airy
            ("1.167.0,88045", il.FeedResult("1.167.0 (88045)", "88045", None)),  # Arc
            ("3.10.8", il.FeedResult("3.10.8 :0294d207:", "9196", None)),  # Vienna
            ("1.2", il.FeedResult("1.2.0", "120", None)),
        ]
        rejected = [
            ("4.0.4", il.FeedResult("404.101", "404.101", None)),  # Yep: different scheme
            ("8.3.3", il.FeedResult("10.2.1", "3084", None)),  # Mate Translate
            ("0.15.14", il.FeedResult("0.15.12", "2370", None)),  # Itsycal: feed behind
            ("33.10.3740-HF1,33.10.3740", il.FeedResult("33.10.3740", "3740", None)),  # hotfix
        ]
        for version, feed in accepted:
            self.assertTrue(il.matches_cask(version, feed), (version, feed))
        for version, feed in rejected:
            self.assertFalse(il.matches_cask(version, feed), (version, feed))


class RenderTests(unittest.TestCase):
    def test_output_compiles_and_is_marked(self):
        candidate = il.Candidate("rocket", "Rocket", "net.matthewpalmer.Rocket", "https://example.com/rocket.xml", "1.9.5,88")
        text = il.render(candidate, il.FeedResult("1.9.5", "88", None), date(2026, 10, 2))
        self.assertTrue(text.startswith(il.GENERATED_MARKER))
        self.assertIn("(1.9.5, build 88)", text)
        bundle_id, entry = build.compile_entry(Path("net.matthewpalmer.Rocket.yml"), build.yaml.safe_load(text))
        self.assertEqual(entry["fallbackSparkleFeed"]["arm64"], "https://example.com/rocket.xml")

    def test_names_needing_quotes(self):
        self.assertEqual(il.yaml_string("Rocket Typist"), "Rocket Typist")
        self.assertEqual(il.yaml_string("Mail: Pro"), '"Mail: Pro"')
        self.assertEqual(il.yaml_string("Über"), '"Über"')

    def test_rewrites_ignore_comment_changes(self):
        self.assertEqual(il.body("# Verified 2026-10-02\nname: A\n"), il.body("# Verified 2026-11-01\nname: A\n"))


if __name__ == "__main__":
    unittest.main()
