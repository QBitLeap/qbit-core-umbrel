# Qbit Core for Umbrel

Independent Qbit Core app source for umbrelOS 2.0 or newer. This is an amd64
prerelease candidate; community-store publication and managed installation
acceptance are still pending.

Core cookies stay inside the Core app. Compatible indexers receive an authenticated, method-limited read endpoint.

The browser UI shows status. Each app instance has explicit Mainnet or
Testnet4 identity and separate persistent data. Existing unmarked data and
wrong-network directories are refused. Wallets, signing and broadcasting are
outside the read API. App credentials are derived per installation and are
never included in source or container images.

## Installation

Install a published release through the QBitLeap community app store:
https://github.com/QBitLeap/umbrel-app-store

The store is not ready until its image lock contains published registry
manifest digests and the complete Mainnet/Testnet4 integration checks pass.
Local image configuration IDs are not downloadable registry references.
Peer networking starts off. Enable QBIT_NETWORK_ACTIVE in app settings when ready to synchronize.

## Source checks

```sh
python3 -m unittest discover -s tests
```

## Image releases

On a Linux amd64 Docker host, from clean committed source:

```sh
python3 scripts/release_images.py 0.1.0-alpha.1
```

That command builds and smoke-checks images. Add `--publish` only after
logging in to GitHub Container Registry with package-write permission. The
manual Image release workflow performs that login with the repository's
GITHUB_TOKEN and emits a receipt containing the actual registry digest.
It has no automatic publication trigger.

Sources, container bases and language dependency locks are pinned. Debian
package resolution is performed at build time; bit-for-bit reproducibility is
not claimed. A rebuilt image must pass the complete store integration checks
before its digest is added to the catalog. Upstream changes require a reviewed
lock/patch update; apps do not adopt upstream changes while running.

## Instance packaging

`scripts/prepare_app_package.py --help` describes the package generator. Choose
an app ID, network and unused UI port; indexers also select a Core dependency.
Use published `ghcr.io/...@sha256:...` image references. Multiple independent
instances reuse an image but need distinct app IDs and writable data folders.
Do not share a writable database or chain directory.

The Core gateway shares its Core service's network namespace. Lifecycle
operations must restart that dependency together; all package services use an
init process so shutdown signals reach the application.

## Licenses

Wrapper code is MIT. See LICENSE, NOTICE and retained upstream notices.
Protocol source, artwork and dependencies keep their respective upstream
licenses. Source releases and image publication are separate from native
macOS/Windows/Linux protocol release artifacts.
