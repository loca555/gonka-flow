"""Export the mints dashboard to IPFS; the read-only API stays on Render."""
import argparse
import html
import json
import re
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"
DEFAULT_API = "https://gonka-flow.onrender.com"
ASSET_URL = re.compile(r"/static/([a-zA-Z0-9._/-]+)")
API_URL = re.compile(r'''(["'`])/api(?=[/?"'`])''')
TEXT_ASSETS = {".js", ".css", ".svg"}


def api_origin(value):
    parsed = urlsplit(value)
    local = parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")
    if (not (parsed.scheme == "https" or local) or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/") or re.search(r'''[\s<>"'`\\]''', value)):
        raise ValueError("API must be an HTTPS origin (HTTP is allowed only for localhost)")
    return value.rstrip("/")


def build(output, api=DEFAULT_API):
    api = api_origin(api)
    output = Path(output)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise ValueError("Output must be an empty directory; existing files are never removed")
    pages = {"index.html": (STATIC / "mints.html").read_text(encoding="utf-8"),
             "api-docs.html": (STATIC / "mints-api.html").read_text(encoding="utf-8")}
    pending = set(ASSET_URL.findall("\n".join(pages.values())))
    assets = {}
    while pending:
        name = pending.pop()
        source = (STATIC / name).resolve()
        if (not source.is_relative_to(STATIC.resolve()) or source.suffix not in TEXT_ASSETS
                or not source.is_file()):
            raise ValueError(f"Unsupported static dependency: {name}")
        content = source.read_text(encoding="utf-8")
        assets[name] = content
        pending.update(set(ASSET_URL.findall(content)) - assets.keys())

    def rewrite(text):
        text = text.replace('href="/api/docs"', 'href="./api-docs.html"')
        text = API_URL.sub(lambda match: match[1] + api + "/api", text)
        return text.replace('/static/', './static/').replace('href="/"', 'href="./"')

    files = {"static/" + name: rewrite(text) for name, text in assets.items()}
    csp = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
           f"connect-src 'self' {api}; img-src 'self' data:; object-src 'none'; base-uri 'self'")
    for name, text in pages.items():
        text = rewrite(text)
        text = text.replace('<meta charset="utf-8">', '<meta charset="utf-8">'
                            '<meta name="referrer" content="no-referrer">'
                            '<meta http-equiv="Content-Security-Policy" content="'
                            + html.escape(csp, quote=True) + '">', 1)
        def version(match):
            digest = sha256(files[match[2]].encode()).hexdigest()[:16]
            return match[1] + "./" + match[2] + "?v=" + digest + '"'
        text = re.sub(r'((?:src|href)=")\./(static/[^"?]+\.(?:js|css))"', version, text)
        files[name] = text
    manifest = {"api": api, "entry": "index.html#trading",
                "files": {name: sha256(content.encode()).hexdigest()
                          for name, content in sorted(files.items())}}
    files["ipfs-build.json"] = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    output.mkdir(parents=True, exist_ok=True)
    for name, content in sorted(files.items()):
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("dist-ipfs"))
    parser.add_argument("--api-origin", default=DEFAULT_API)
    args = parser.parse_args()
    manifest = build(args.output, args.api_origin)
    print(json.dumps({"output": str(args.output), "api": manifest["api"],
                      "files": len(manifest["files"]) + 1}))
