# npm release runbook

TinyIC's npm package is a zero-npm-dependency CLI launcher that bundles the
locked Python workspace. It is separate from the deliberately unpublished
Python distributions. Publishing is a maintainer action; installing a public
package does not require an npm account.

`npm install` lays down the launcher and source bundle only. The launcher is
currently validated with `uv` 0.7.12+. On its first run, `tinyic` may download
Python 3.12 plus roughly 284 locked Python packages and populate the user's
versioned runtime cache. It is not an offline, self-contained executable.

## Account setup

The first publisher needs a free [npm account](https://www.npmjs.com/signup), a
verified email address, and npm's required publishing protection. Enable
two-factor authentication for authorization and writes before publishing; see
[npm's 2FA guidance](https://docs.npmjs.com/requiring-2fa-for-package-publishing-and-settings-modification/).

The unscoped name `tinyic` was unclaimed when this package was prepared on
2026-07-15. Availability is not a reservation, so check it again immediately
before the release:

```bash
npm view tinyic name version --registry=https://registry.npmjs.org/
```

An `E404` means no public package currently owns the name. If a package is
returned, stop and choose an approved scope/name; do not publish under a
look-alike name.

## Release gate

Release from a fresh clean worktree/clone so unrelated local files do not need
to be deleted. Use only a reviewed `main` commit with an exact matching version
tag. `package.json` and `src/tinyic/pyproject.toml` must carry the same version.
The commands below must produce no live provider/API calls.

```bash
npm login --registry=https://registry.npmjs.org/
npm whoami --registry=https://registry.npmjs.org/

git status --porcelain=v1       # must print nothing
git branch --show-current       # must print main
version="$(node -p 'require("./package.json").version')"
test "$(git describe --tags --exact-match)" = "v$version"
uv --version                    # must be 0.7.12 or newer
uv lock --check --offline
npm run test:offline
npm pack --dry-run
```

The current local gate is macOS. Add clean supported-LTS Node jobs for every
platform the release will claim; in particular, do not claim Windows support
until the real install, stdin steering, signal, keyring, and browser paths pass
there.

For the first release only, prepare—but do not push—a final documentation
commit that removes the pending-`E404` warning and makes npm the primary install
path. Create its exact matching tag locally so the release gate above validates
the revision, but do not push the commit or tag yet. Publish the tested tarball
first, then immediately push that exact commit and its pre-created tag. If
publication fails, do not push install instructions for the still-unowned name.

Create and exercise the exact tarball before sending it to the registry:

```bash
npm pack --json > /tmp/tinyic-npm-pack.json
cat /tmp/tinyic-npm-pack.json

prefix="$(mktemp -d)"
npm install --global --prefix "$prefix" ./tinyic-2.2.0.tgz \
  --offline --ignore-scripts --no-audit --no-fund
PATH="$prefix/bin:$PATH" \
  TINYIC_NPM_CACHE_DIR="$prefix/cache" \
  UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never \
  tinyic --version

npm publish --dry-run ./tinyic-2.2.0.tgz \
  --access public --registry=https://registry.npmjs.org/
```

Inspect the `npm pack --dry-run` list. It must contain the two source trees,
root lock/config, launcher, README, license, and interface screenshots. It must
not contain tests, artifacts, credentials, logs, virtual environments, build
trees, egg-info, or inherited untracked files.

## First publication

The initial unscoped release is public:

```bash
npm publish ./tinyic-2.2.0.tgz \
  --access public --registry=https://registry.npmjs.org/
npm view tinyic@2.2.0 name version dist-tags dist.integrity --json \
  --registry=https://registry.npmjs.org/
```

Compare the registry's `dist.integrity` with the `integrity` value saved in
`/tmp/tinyic-npm-pack.json`. The explicit full gate is required because the
tested tarball—not a newly packed working directory—is published. Never use
`--force`, and never reuse a published version. If a release is bad, prefer a
patch release and `npm deprecate` guidance over unpublishing it.

After the first manual release establishes the package, configure
[npm trusted publishing](https://docs.npmjs.com/trusted-publishers/) for the
repository and use provenance-bearing CI releases. The publishing job must use
Node.js 22.14+ and npm 11.5.1+ for trusted publishing. Keep the number of
package owners small and review it with `npm owner ls tinyic` after ownership
changes.
