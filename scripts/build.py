# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml==6.0.2"]
# ///
"""Validate every catalog entry in apps/ and compile them into dist/index.json.

    uv run scripts/build.py           # validate and build dist/index.json
    uv run scripts/build.py --check   # validate only (what CI runs on pull requests)

Ripe clients read only the compiled JSON, never the YAML, so every rule that protects
clients lives here. Entries are untrusted input from pull requests: validation is strict
and rejects unknown keys, so a typo fails loudly instead of being silently ignored.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

SCHEMA_VERSION = 1
ROOT = Path(__file__).resolve().parent.parent

BUNDLE_ID = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
CASK_TOKEN = re.compile(r"^[a-z0-9][a-z0-9@+._-]*$")
ARCHITECTURES = ("arm64", "x86_64")
# Where an app may keep its real version. Ripe only lists these folders; it never reads files.
VERSION_GLOB_ROOTS = ("~/Library/", "/Library/", "/Applications/")
KNOWN_KEYS = {"bundle_id", "name", "sparkle_feed", "homebrew_cask", "installed_version", "notes"}
DIRECTIVES = {"sparkle_feed", "homebrew_cask", "installed_version", "notes"}
MAX_NOTES = 500


class Invalid(Exception):
    pass


def https_url(value: object, field: str) -> str:
    if not isinstance(value, str) or not re.match(r"^https://[^\s/]+\.[^\s/]+/\S*$", value):
        raise Invalid(f"{field} must be an https:// URL")
    return value


def compile_entry(path: Path, data: object) -> tuple[str, dict]:
    """Returns (bundle_id, compiled entry) or raises Invalid with every problem found."""
    if not isinstance(data, dict):
        raise Invalid("must be a YAML mapping")
    problems: list[str] = []

    def check(fn):
        try:
            return fn()
        except Invalid as error:
            problems.append(str(error))
            return None

    unknown = set(data) - KNOWN_KEYS
    if unknown:
        problems.append(f"unknown keys: {', '.join(sorted(unknown))} (allowed: {', '.join(sorted(KNOWN_KEYS))})")

    bundle_id = data.get("bundle_id")
    if not isinstance(bundle_id, str) or not BUNDLE_ID.match(bundle_id):
        problems.append("bundle_id must be a reverse-DNS identifier like com.example.App")
    elif path.name != f"{bundle_id}.yml":
        problems.append(f"file must be named {bundle_id}.yml")

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        problems.append("name is required")

    if not DIRECTIVES & set(data):
        problems.append(f"needs at least one of: {', '.join(sorted(DIRECTIVES))}")

    entry: dict = {"name": name}

    if "sparkle_feed" in data:
        feed = data["sparkle_feed"]

        def sparkle():
            # One URL for every Mac, or one per CPU. Always compiled to the per-CPU form.
            if isinstance(feed, str):
                url = https_url(feed, "sparkle_feed")
                return {arch: url for arch in ARCHITECTURES}
            if isinstance(feed, dict) and feed and set(feed) <= set(ARCHITECTURES):
                return {arch: https_url(url, f"sparkle_feed.{arch}") for arch, url in feed.items()}
            raise Invalid(f"sparkle_feed must be a URL or a mapping with keys {' and/or '.join(ARCHITECTURES)}")

        entry["sparkleFeed"] = check(sparkle)

    if "homebrew_cask" in data:
        token = data["homebrew_cask"]
        if not isinstance(token, str) or not CASK_TOKEN.match(token):
            problems.append("homebrew_cask must be a cask token like brave-browser")
        entry["homebrewCask"] = token

    if "installed_version" in data:

        def installed_version():
            rule = data["installed_version"]
            if not isinstance(rule, dict) or set(rule) != {"glob"} or not isinstance(rule["glob"], str):
                raise Invalid("installed_version must be a mapping with a single glob key")
            glob = rule["glob"]
            last = glob.rsplit("/", 1)[-1]
            if not glob.startswith(VERSION_GLOB_ROOTS):
                raise Invalid(f"installed_version.glob must start with one of {', '.join(VERSION_GLOB_ROOTS)}")
            if ".." in glob.split("/") or glob.count("*") != 1 or "*" not in last:
                raise Invalid("installed_version.glob needs exactly one *, in the file name, standing for the version")
            return {"glob": glob}

        entry["installedVersion"] = check(installed_version)

    if "notes" in data:
        notes = data["notes"]
        if not isinstance(notes, str) or not notes.strip() or len(notes) > MAX_NOTES:
            problems.append(f"notes must be text of at most {MAX_NOTES} characters")
        else:
            entry["notes"] = " ".join(notes.split())

    if problems:
        raise Invalid("; ".join(problems))
    return bundle_id, {key: value for key, value in entry.items() if value is not None}


def build(apps_dir: Path) -> tuple[dict, list[str]]:
    apps: dict[str, dict] = {}
    seen: dict[str, str] = {}
    errors: list[str] = []
    for path in sorted(apps_dir.glob("*")):
        if path.name.startswith("."):
            continue
        if path.suffix != ".yml":
            errors.append(f"{path.name}: catalog files must end in .yml")
            continue
        try:
            with path.open(encoding="utf-8") as handle:
                bundle_id, entry = compile_entry(path, yaml.safe_load(handle))
        except yaml.YAMLError as error:
            errors.append(f"{path.name}: invalid YAML: {error}")
            continue
        except Invalid as error:
            errors.append(f"{path.name}: {error}")
            continue
        # Bundle IDs are case-insensitive on macOS; two files differing only in case collide.
        key = bundle_id.lower()
        if key in seen:
            errors.append(f"{path.name}: same bundle ID as {seen[key]}")
            continue
        seen[key] = path.name
        apps[bundle_id] = entry
    index = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "apps": apps,
    }
    return index, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="validate only, don't write dist/index.json")
    args = parser.parse_args()

    index, errors = build(ROOT / "apps")
    if errors:
        print(f"{len(errors)} invalid catalog file(s):", file=sys.stderr)
        for error in errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    print(f"{len(index['apps'])} entries valid")
    if not args.check:
        out = ROOT / "dist" / "index.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(index, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
