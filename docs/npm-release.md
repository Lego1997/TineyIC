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
Use the interactive browser-backed login for the first release; do not create a
long-lived publish token. GitHub has
[announced](https://github.blog/changelog/2026-07-08-npm-install-time-security-and-gat-bypass2fa-deprecation/)
that bypass-2FA granular tokens will lose direct-publish access, while OIDC
trusted publishing is the supported automation path.

The unscoped name `tinyic` was unclaimed when this package was prepared on
2026-07-16. Availability is not a reservation, so check it again immediately
before the release:

```bash
npm view tinyic name version --registry=https://registry.npmjs.org/
```

An `E404` means no public package currently owns the name. If a package is
returned, stop and choose an approved scope/name; do not publish under a
look-alike name.

Neither trusted publishing nor staged publishing can create a brand-new npm
package. The first `tinyic` release therefore has to be an interactive direct
publish with 2FA. Every later release uses the staged GitHub OIDC path below.

## Release gate

Release from a fresh clean worktree/clone so unrelated local files do not need
to be deleted. Use only a reviewed `main` commit with an exact matching version
tag. `package.json` and `src/tinyic/pyproject.toml` must carry the same version.
The commands below must produce no live provider/API calls.

```bash
test "$(node -p 'process.versions.node.split(".")[0]')" = "24"
npm install --global npm@11.18.0 --ignore-scripts --no-audit --no-fund
test "$(npm --version)" = "11.18.0"
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

The required `.github/workflows/npm-package.yml` gate tests the declared
Node/uv floor on Ubuntu, the current Node 24/uv lane on Ubuntu, and the current
lane on macOS. The package metadata permits macOS and Linux only. Do not remove
that restriction or claim Windows support until the real install, stdin
steering, signal, keyring, and browser paths pass there.

For the first release only, prepare—but do not push—a final documentation
commit that removes the pending-`E404` warning and makes npm the primary install
path. Create its exact matching tag locally so the release gate above validates
the revision, but do not push the commit or tag yet. Publish the tested tarball
under `next`, verify it, then push that exact commit and its pre-created tag
before promoting `latest`. If publication fails, do not push install
instructions for the still-unowned name.

Create and exercise the exact tarball before sending it to the registry:

```bash
npm pack --json > /tmp/tinyic-npm-pack.json
cat /tmp/tinyic-npm-pack.json
tarball="$(node -e 'const fs=require("node:fs"); const [p]=JSON.parse(fs.readFileSync(process.argv[1],"utf8")); process.stdout.write(p.filename)' /tmp/tinyic-npm-pack.json)"

TINYIC_NPM_TARBALL="$tarball" \
  uv run --offline pytest -q tests/test_npm_packaging.py

prefix="$(mktemp -d)"
npm install --global --prefix "$prefix" "$tarball" \
  --offline --ignore-scripts --no-audit --no-fund
PATH="$prefix/bin:$PATH" \
  TINYIC_NPM_CACHE_DIR="$prefix/cache" \
  UV_OFFLINE=1 UV_PYTHON_DOWNLOADS=never \
  tinyic --version

npm publish --dry-run "$tarball" \
  --access public --registry=https://registry.npmjs.org/
```

Inspect the `npm pack --dry-run` list. It must contain the two source trees,
root lock/config, launcher, README, license, and interface screenshots. It must
not contain tests, artifacts, credentials, logs, virtual environments, build
trees, egg-info, or inherited untracked files.

## First publication

The initial unscoped release cannot be staged. Publish the already-tested
tarball under the non-default `next` tag, complete the 2FA challenge, compare
its registry integrity, and install the explicit version before exposing it as
`latest`:

```bash
npm publish "$tarball" --tag next \
  --access public --registry=https://registry.npmjs.org/
npm view tinyic@2.2.0 name version dist-tags dist.integrity --json \
  --registry=https://registry.npmjs.org/

first_install="$(mktemp -d)"
npm install --global --prefix "$first_install" tinyic@2.2.0 \
  --ignore-scripts --no-audit --no-fund \
  --registry=https://registry.npmjs.org/
PATH="$first_install/bin:$PATH" tinyic --version
```

Compare the registry's `dist.integrity` with the `integrity` value saved in
`/tmp/tinyic-npm-pack.json`. The explicit full gate is required because the
tested tarball—not a newly packed working directory—is published. If those
checks pass, push the prepared commit and exact tag, then promote the verified
version:

```bash
git push --atomic origin main v2.2.0
npm dist-tag add tinyic@2.2.0 latest \
  --registry=https://registry.npmjs.org/
npm view tinyic@2.2.0 dist-tags dist.integrity --json \
  --registry=https://registry.npmjs.org/
```

Never use `--force`, and never reuse a published version. If a release is bad,
prefer a patch release and `npm deprecate` guidance over unpublishing it.
If branch protection rejects the atomic Git push, leave the verified package on
`next`, resolve the repository permissions without force-pushing, and retry the
same atomic push before assigning `latest`.

## Trusted staged releases after 2.2.0

After the first manual release establishes the package, create a protected
GitHub environment named `npm-production` with a required reviewer and a
deployment tag rule that permits only `v*`; protect the same release-tag pattern
with a repository ruleset. Then use npm 11.15+ (the repository pins 11.18.0) to
configure the package's one trusted publisher. Grant staging only—never direct
publish:

```bash
npm install --global npm@11.18.0
npm trust github tinyic \
  --file publish-npm.yml \
  --repository Lego1997/TineyIC \
  --environment npm-production \
  --allow-stage-publish \
  --registry=https://registry.npmjs.org/
npm trust list tinyic --registry=https://registry.npmjs.org/
```

In npm package settings, select **Require two-factor authentication and disallow
tokens** after the trust relationship is verified. The workflow uses a
GitHub-hosted runner, Node 24, npm 11.18.0, and uv 0.11.28 with caches disabled;
it validates an exact tag reachable from `main`, runs the complete offline
suite, packs and retests one tarball, verifies its SHA-256 after artifact
transfer, and stages that same tarball through OIDC. No `NPM_TOKEN` or
`NODE_AUTH_TOKEN` is used. Trusted GitHub publishing adds provenance
automatically.

For a later version whose exact tag already exists on `main`:

```bash
gh workflow run publish-npm.yml --ref v2.2.1 -f tag=v2.2.1
```

After the workflow succeeds, inspect the entry under npm's **Staged Packages**,
download it if desired, and approve it with 2FA. Staged publishing requires npm
11.15+ and an existing package; basic trusted publishing alone requires npm
11.5.1+. Keep the number of package owners small and review it with
`npm owner ls tinyic` after ownership changes.
