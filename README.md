# Gonka Flow

WGNK bridge and WGNK / USDT trading monitor. Read only.

## Local run

```sh
python -m venv .venv
# Activate .venv and install dependencies:
pip install -r requirements.txt
```

Set `APP_MODE=mints`, `LOAD_SEED_ARCHIVE=true`, then run:

```sh
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
python -m unittest discover -s tests
```

## IPFS

```sh
python tools/build_ipfs.py --output dist-ipfs
ipfs add -r --cid-version=1 dist-ipfs
```

Open `/ipfs/<CID>/#trading` through the gateway of your own IPFS node. Use a new empty folder for a rebuild.

The interface is published to IPFS. Live data comes from the public API `https://gonka-flow.onrender.com`; the server and indexer stay on Render. Another API can be set with `--api-origin https://example.com` — it must allow GET requests from IPFS gateways via CORS.
