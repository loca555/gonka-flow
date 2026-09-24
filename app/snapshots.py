"""Crash-recovery snapshots: rolling public seed archives on GitHub Releases.

The service disk is ephemeral and the deploy-bundled seed ages quickly, so
after a crash the indexer used to resync tens of thousands of blocks. Every
SNAPSHOT_HOURS the running instance exports the reconciled public seed and
publishes it as assets of the single `auto-snapshot` release; at boot the
fresher of the bundled seed and that release is restored, so resync starts
from recent blocks. Reading a public repo is anonymous; publishing needs a
fine-grained token in BACKUP_GITHUB_TOKEN (Contents: read+write, this repo).
"""
import asyncio
import hashlib
import json
import shutil
import tempfile
import time
from pathlib import Path

import httpx

from .seed import DEFAULT_ARCHIVE, export_seed, restore_seed

GITHUB_API = "https://api.github.com"
TAG = "auto-snapshot"


def _headers(token=None):
    headers = {"accept": "application/vnd.github+json", "user-agent": "gonka-flow/1.0",
               "x-github-api-version": "2022-11-28"}
    if token:
        headers["authorization"] = "Bearer " + token
    return headers


async def _release(client, repo):
    response = await client.get(f"{GITHUB_API}/repos/{repo}/releases/tags/{TAG}")
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


async def _download(client, url, target, expected_sha256):
    digest = hashlib.sha256()
    async with client.stream("GET", url, headers={"accept": "application/octet-stream"}) as response:
        response.raise_for_status()
        with open(target, "wb") as raw:
            async for chunk in response.aiter_bytes():
                raw.write(chunk)
                digest.update(chunk)
    if digest.hexdigest() != expected_sha256:
        raise ValueError("Snapshot archive hash mismatch")


def _bundled_manifest():
    manifest_path = DEFAULT_ARCHIVE.parent / "manifest.json"
    return json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}


async def restore_fresh(target, cfg):
    """Restore the freshest available archive: deploy-bundled seed or the
    auto-snapshot release. Never overwrites an existing runtime database."""
    if Path(target).exists():
        return False
    if cfg.snapshot_restore:
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True,
                                         headers=_headers()) as client:
                release = await _release(client, cfg.snapshot_repo)
                assets = {a["name"]: a for a in (release or {}).get("assets", [])}
                if "wgnk.sqlite3.gz" in assets and "manifest.json" in assets:
                    manifest = json.loads((await client.get(
                        assets["manifest.json"]["url"],
                        headers={"accept": "application/octet-stream"})).text)
                    if int(manifest.get("height") or 0) > int(_bundled_manifest().get("height") or 0):
                        with tempfile.TemporaryDirectory(prefix="gonka-snapshot-") as tmp:
                            archive = Path(tmp) / "wgnk.sqlite3.gz"
                            await _download(client, assets["wgnk.sqlite3.gz"]["url"],
                                            archive, manifest["sha256"])
                            return restore_seed(Path(target), archive)
        except Exception:
            pass  # Any snapshot problem falls back to the bundled seed.
    return restore_seed(Path(target), DEFAULT_ARCHIVE)


async def publish_snapshot(cfg, db_path, current_height):
    """Export the public seed and replace the auto-snapshot release assets."""
    if not cfg.snapshot_token:
        raise RuntimeError("BACKUP_GITHUB_TOKEN is not configured")
    with tempfile.TemporaryDirectory(prefix="gonka-publish-") as tmp:
        await asyncio.to_thread(export_seed, db_path, tmp)
        manifest = json.loads((Path(tmp) / "manifest.json").read_text(encoding="utf-8"))
        if int(manifest["height"]) != int(current_height):
            raise ValueError("Snapshot moved during export")
        async with httpx.AsyncClient(timeout=600, follow_redirects=True,
                                     headers=_headers(cfg.snapshot_token)) as client:
            release = await _release(client, cfg.snapshot_repo)
            if release:
                (await client.delete(f"{GITHUB_API}/repos/{cfg.snapshot_repo}/releases/{release['id']}")).raise_for_status()
                (await client.delete(f"{GITHUB_API}/repos/{cfg.snapshot_repo}/git/refs/tags/{TAG}")).raise_for_status()
            created = await client.post(f"{GITHUB_API}/repos/{cfg.snapshot_repo}/releases", json={
                "tag_name": TAG, "name": "Auto snapshot", "prerelease": True,
                "body": f"Rolling crash-recovery seed, block {manifest['height']}."})
            created.raise_for_status()
            upload = created.json()["upload_url"].split("{")[0]
            for name, content_type in (("wgnk.sqlite3.gz", "application/gzip"),
                                       ("manifest.json", "application/json")):
                data = (Path(tmp) / name).read_bytes()
                uploaded = await client.post(upload, params={"name": name},
                                             headers={"content-type": content_type}, content=data)
                uploaded.raise_for_status()
        return int(manifest["height"])
