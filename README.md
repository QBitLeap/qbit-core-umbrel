# Qbit Core for Umbrel

Independent Qbit Core app source for umbrelOS 2.0 or newer. This is an amd64 app. Published images have passed isolated Mainnet/Testnet4 integration;
managed Umbrel installation and real-chain operation acceptance remain pending.

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
New installations start peer synchronization automatically. The dashboard shows
current blocks, known headers, verification progress and peer connections.
Use its networking switch to pause or resume; downloaded blocks are preserved.
The switch uses Umbrel's app access settings. Open Qbit Core through Umbrel.
Read/API credentials cannot change networking or access wallets.
Local wallets are enabled by default for LEAP and qord terminal commands.
Wallet RPC stays on Core's loopback interface.

The networking choice is saved in this app's own `control` volume and
survives restarts and app updates. A fresh chain directory uses the default. `QBIT_NETWORK_ACTIVE` sets the initial/default choice;
changing that environment value resets a previously saved dashboard choice.
The broker's Core-cookie mount remains read-only; the UI has no cookie mount.

Dashboard refresh defaults to one minute. Set another whole number of minutes
on the page; the minimum is one minute. The preference is saved in this browser,
refreshes stop while the page is hidden, and requests never overlap.

## Source checks

```sh
python3 -m unittest discover -s tests
```

## Image releases

On a Linux amd64 Docker host, from clean committed source:

```sh
python3 scripts/release_images.py 0.1.1
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
