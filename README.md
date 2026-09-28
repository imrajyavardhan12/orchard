# orchard

The open app catalog behind [Ripe](https://github.com/imrajyavardhan12/ripe). One small YAML file per app teaches Ripe something it can't work out by itself: where an app's update feed is, which Homebrew cask it is, or where its real version lives.

Ripe already checks most apps without help (App Store, Sparkle feeds declared in the app, and Homebrew's cask database). orchard covers the rest, and fixes wrong answers for everyone at once.

## Add or fix an app

1. Find the app's bundle ID: `osascript -e 'id of app "Brave Browser"'`.
2. Create `apps/<bundle-id>.yml` (see the schema below).
3. Check it and try it with Ripe before opening a pull request:

   ```sh
   uv run scripts/build.py
   RIPE_CATALOG_URL="file://$PWD/dist/index.json" ripe why "Brave Browser"
   ```

   `ripe why` shows `orchard` in its sources when your entry was applied.
4. Open a pull request. CI runs the same validation.

A good pull request says how you verified the entry (for example, "the feed's newest `sparkle:version` equals the `CFBundleVersion` of the current release").

## Schema

```yaml
bundle_id: com.brave.Browser        # required; must match the file name
name: Brave Browser                 # required; as shown in Finder

# At least one of the following:

sparkle_feed: https://example.com/appcast.xml   # one feed for every Mac, or:
sparkle_feed:
  arm64: https://…/stable-arm64/appcast.xml     # Apple silicon
  x86_64: https://…/stable/appcast.xml          # Intel

homebrew_cask: brave-browser        # pin the cask when Ripe's automatic match is wrong or ambiguous

installed_version:                  # for apps that update in place without changing their bundle
  glob: "~/Library/Application Support/obsidian/obsidian-*.asar"

notes: Shown in `ripe why`. Explain anything surprising about how this app updates.
```

| Key | When to use it |
|---|---|
| `sparkle_feed` | The app uses Sparkle but sets its feed in code, so it isn't in `Info.plist`. Overrides the app's own feed if both exist (use that to fix a dead feed). Only `https://`. |
| `homebrew_cask` | Ripe matched the wrong cask, or several casks install the same app. |
| `installed_version.glob` | The app's bundle version is stale by design. `*` stands for the version; Ripe lists the folder and takes the highest match. Must be under `~/Library/`, `/Library/` or `/Applications/`, with exactly one `*` in the file name. Ripe only lists the folder; it never opens files. |
| `notes` | Up to 500 characters. |

Unknown keys are rejected, so typos fail in CI instead of being ignored.

## How it's served

On every merge to `main`, CI compiles all entries into one `index.json` and publishes it with GitHub Pages. Ripe downloads it at most every 6 hours, caches it, and works without it. No server, no cost.

## Trust

Catalog entries are untrusted input, and Ripe treats them that way. An entry can change *which version Ripe compares against*, but it can never weaken installation checks: when Ripe installs an update (v0.2), the download must pass its checksum or EdDSA signature, a valid code signature, and a **Team ID match with the app you already have**, whatever the catalog says.

## License

The catalog data is [CC0-1.0](LICENSE): use it for anything, no attribution required.
