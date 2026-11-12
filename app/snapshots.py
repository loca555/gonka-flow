"""Public backups: validate a draft before publishing; never delete old copies."""
import asyncio
import hashlib
import json
import logging
import re
import sqlite3
import tempfile
import zlib
from pathlib import Path
from xml.etree import ElementTree

import httpx
from .seed import DEFAULT_ARCHIVE, MAX_UNPACKED_SEED_BYTES, export_seed, restore_seed

GITHUB_API = "https://api.github.com"
GITHUB_WEB = "https://github.com"
TAG = "auto-snapshot"
PREFIX = "flow-snapshot-"
VERSIONED_TAG = re.compile(r"flow-snapshot-([1-9][0-9]{0,15})-([0-9a-f]{12})")
ATOM = "{http://www.w3.org/2005/Atom}"
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_FEED_BYTES = 256 * 1024
REMOTE_ERRORS = (KeyError, OSError, ValueError, EOFError, sqlite3.Error, zlib.error, ElementTree.ParseError, httpx.HTTPError)
log = logging.getLogger("uvicorn.error.flow_snapshot")


def _headers(token=None):
    h = {"accept": "application/vnd.github+json", "user-agent": "gonka-flow/1.0",
         "x-github-api-version": "2022-11-28"}
    if token:
        h["authorization"] = "Bearer " + token
    return h


def _repo(repo):
    if not re.fullmatch(r"loca555/(?!\.{1,2}$)[A-Za-z0-9_.-]+", repo):
        raise ValueError("Snapshot repository must belong to loca555")
    return repo


def _id(value):
    if type(value) is not int or value <= 0:
        raise ValueError("Invalid GitHub resource ID")
    return value


def _manifest(v):
    if not isinstance(v, dict) or type(v.get("format")) is not int or v["format"] != 1 or v.get("network") != "ethereum":
        raise ValueError("Unsupported snapshot manifest")
    for key, limit in (("height", 2**53-1), ("unpacked_bytes", MAX_UNPACKED_SEED_BYTES)):
        if type(v.get(key)) is not int or not 0 < v[key] <= limit:
            raise ValueError("Invalid snapshot " + key)
    if not re.fullmatch(r"[0-9a-f]{64}", str(v.get("sha256", ""))):
        raise ValueError("Invalid snapshot hash")
    size = v.get("archive_bytes")
    if size is not None and (type(size) is not int or not 0 < size <= MAX_ARCHIVE_BYTES):
        raise ValueError("Invalid snapshot archive size")
    return v


def _asset_url(repo, asset):
    # Ignore response-supplied URLs; construct a trusted GitHub API destination.
    return f"{GITHUB_API}/repos/{_repo(repo)}/releases/assets/{_id(asset['id'])}"


async def _read_limited(client, url, limit, accept):
    data = bytearray()
    async with client.stream("GET", url, headers={"accept": accept}) as r:
        r.raise_for_status()
        async for chunk in r.aiter_bytes():
            if len(data) + len(chunk) > limit:
                raise ValueError("Snapshot metadata exceeds size limit")
            data.extend(chunk)
    return data


async def _read_manifest(client, url):
    return _manifest(json.loads(await _read_limited(client, url, MAX_MANIFEST_BYTES, "application/octet-stream")))


async def _download(client, url, target, expected_sha256, expected_size=None):
    digest, size = hashlib.sha256(), 0
    limit = MAX_ARCHIVE_BYTES if expected_size is None else min(expected_size, MAX_ARCHIVE_BYTES)
    async with client.stream("GET", url, headers={"accept": "application/octet-stream"}) as r:
        r.raise_for_status()
        with open(target, "wb") as raw:
            async for chunk in r.aiter_bytes():
                size += len(chunk)
                if size > limit:
                    raise ValueError("Snapshot download exceeds size limit")
                raw.write(chunk)
                digest.update(chunk)
    if expected_size is not None and size != expected_size:
        raise ValueError("Snapshot download size mismatch")
    if digest.hexdigest() != expected_sha256:
        raise ValueError("Snapshot archive hash mismatch")


def _bundled_manifest():
    p = DEFAULT_ARCHIVE.parent / "manifest.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


async def _releases(client, repo):
    base = f"{GITHUB_API}/repos/{_repo(repo)}/releases"
    r = await client.get(base, params={"per_page": 20})
    if r.status_code == 404:
        return []
    r.raise_for_status()
    values = r.json()
    if not isinstance(values, list):
        raise ValueError("Invalid release list")
    found = []
    for v in values:
        if not isinstance(v, dict) or v.get("draft"):
            continue
        tag = v.get("tag_name", "")
        if not isinstance(tag, str):
            continue
        m = VERSIONED_TAG.fullmatch(tag)
        if tag == TAG or m:
            found.append((int(m[1]) if m else 0, v))
    if not any(v.get("tag_name") == TAG for _, v in found):
        try:
            legacy = await client.get(base + "/tags/" + TAG)
            if legacy.status_code != 404:
                legacy.raise_for_status()
                v = legacy.json()
                if isinstance(v, dict) and not v.get("draft"):
                    found.append((0, v))
        except (ValueError, httpx.HTTPError) as error:
            _log_rejection("legacy release lookup", error)
    newest = [v for h, v in sorted(found, key=lambda item: item[0], reverse=True) if h > 0][:5]
    legacy = [v for _, v in found if v.get("tag_name") == TAG]
    return newest + legacy[:1]


def _log_rejection(stage, error):
    status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else "-"
    # Exception messages can contain a temporary signed asset URL.
    log.warning("Flow snapshot %s failed (%s, HTTP %s)", stage, type(error).__name__, status)


async def _restore_archive(client, target, url, manifest):
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gonka-snapshot-", dir=target.parent) as tmp:
        archive = Path(tmp) / "wgnk.sqlite3.gz"
        (Path(tmp) / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        await _download(client, url, archive, manifest["sha256"], manifest.get("archive_bytes"))
        return restore_seed(target, archive)


async def _restore_from_feed(client, target, repo, minimum):
    base = f"{GITHUB_WEB}/{_repo(repo)}/releases"
    data = (await _read_limited(client, base + ".atom", MAX_FEED_BYTES, "application/atom+xml")).decode("utf-8")
    if "<!DOCTYPE" in data or "<!ENTITY" in data:
        raise ValueError("Unexpected XML declarations in snapshot feed")
    feed = ElementTree.fromstring(data)
    prefix = base + "/tag/"
    tags = {}
    for link in feed.findall(f"{ATOM}entry/{ATOM}link"):
        href = link.get("href", "")
        if link.get("rel", "alternate") != "alternate" or not href.startswith(prefix):
            continue
        tag = href[len(prefix):]
        match = VERSIONED_TAG.fullmatch(tag)
        if match and int(match[1]) > minimum:
            tags[tag] = (int(match[1]), match[2])
    for tag, (height, digest) in sorted(tags.items(), key=lambda item: item[1][0], reverse=True)[:5]:
        if target.exists():
            return False
        directory = f"{base}/download/{tag}"
        try:
            manifest = await _read_manifest(client, directory + "/manifest.json")
            if manifest["height"] != height or not manifest["sha256"].startswith(digest):
                raise ValueError("Snapshot manifest does not match release tag")
            if await _restore_archive(client, target, directory + "/wgnk.sqlite3.gz", manifest):
                log.info("Flow snapshot restored from GitHub Releases at block %s", height)
                return True
        except REMOTE_ERRORS as error:
            _log_rejection(f"GitHub Releases block {height}", error)
    return False


async def _restore_from_api(client, target, repo, minimum):
    candidates = []
    for release in await _releases(client, repo):
        try:
            values = release.get("assets")
            if not isinstance(values, list):
                continue
            assets = {a["name"]: a for a in values if isinstance(a, dict) and isinstance(a.get("name"), str)}
            manifest = await _read_manifest(client, _asset_url(repo, assets["manifest.json"]))
            if manifest["height"] > minimum:
                candidates.append((manifest, _asset_url(repo, assets["wgnk.sqlite3.gz"])))
        except REMOTE_ERRORS as error:
            _log_rejection("GitHub API manifest", error)
    for manifest, url in sorted(candidates, key=lambda x: x[0]["height"], reverse=True):
        if target.exists():
            return False
        try:
            if await _restore_archive(client, target, url, manifest):
                log.info("Flow snapshot restored from GitHub API at block %s", manifest["height"])
                return True
        except REMOTE_ERRORS as error:
            _log_rejection(f"GitHub API block {manifest['height']}", error)
    return False


async def restore_fresh(target, cfg):
    """Restore a verified public snapshot without overwriting an existing DB."""
    target = Path(target)
    if target.exists():
        return False
    bundled_height = int(_bundled_manifest().get("height") or 0)
    if cfg.snapshot_restore:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True, headers=_headers()) as client:
            for source, restore in (("GitHub Releases feed", _restore_from_feed), ("GitHub API", _restore_from_api)):
                try:
                    async with asyncio.timeout(30):
                        if await restore(client, target, cfg.snapshot_repo, bundled_height):
                            return True
                except REMOTE_ERRORS as error:
                    _log_rejection(source, error)
                if target.exists():
                    return False
        log.warning("Flow snapshot: no newer verified remote backup, using bundled block %s", bundled_height)
    return restore_seed(target, DEFAULT_ARCHIVE)


async def publish_snapshot(cfg, db_path, current_height):
    """Upload and verify a draft, then publish it; never delete earlier backups."""
    if not cfg.snapshot_token:
        raise RuntimeError("BACKUP_GITHUB_TOKEN is not configured")
    repo = _repo(cfg.snapshot_repo)
    with tempfile.TemporaryDirectory(prefix="gonka-publish-") as tmp:
        await asyncio.to_thread(export_seed, db_path, tmp)
        manifest = _manifest(json.loads((Path(tmp) / "manifest.json").read_text(encoding="utf-8")))
        if manifest["height"] < int(current_height):
            raise ValueError("Snapshot is older than the requested finalized height")
        if not await asyncio.to_thread(restore_seed, Path(tmp) / "verified.sqlite3", Path(tmp) / "wgnk.sqlite3.gz"):
            raise ValueError("Snapshot restoration was not verified")
        tag = f"{PREFIX}{manifest['height']}-{manifest['sha256'][:12]}"
        base = f"{GITHUB_API}/repos/{repo}/releases"
        async with httpx.AsyncClient(timeout=600, follow_redirects=True,
                                     headers=_headers(cfg.snapshot_token)) as client:
            created = await client.post(base, json={
                "tag_name": tag, "name": f"Public snapshot {manifest['height']}",
                "draft": True, "prerelease": True,
                "body": f"Verified public crash-recovery seed, finalized block {manifest['height']}."})
            created.raise_for_status()
            release_id = _id(created.json()["id"])
            upload = f"https://uploads.github.com/repos/{repo}/releases/{release_id}/assets"
            for name, content_type in (("wgnk.sqlite3.gz", "application/gzip"), ("manifest.json", "application/json")):
                data = (Path(tmp) / name).read_bytes()
                uploaded = await client.post(upload, params={"name": name},
                                             headers={"content-type": content_type}, content=data)
                uploaded.raise_for_status()
                asset = uploaded.json()
                if (asset.get("name") != name or asset.get("state") != "uploaded"
                        or asset.get("size") != len(data)
                        or asset.get("digest") != "sha256:" + hashlib.sha256(data).hexdigest()):
                    raise ValueError("Uploaded snapshot asset was not verified")
            published = await client.patch(base + f"/{release_id}", json={"draft": False})
            published.raise_for_status()
            if published.json().get("draft") is not False:
                raise ValueError("Snapshot release is still a draft")
        return manifest["height"]
