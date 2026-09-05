"""Read-only public endpoint probe. No wallet, signing, or private credentials."""
import concurrent.futures
import json
import urllib.request

TARGETS = {
    "gonka2": "https://node2.gonka.ai:8443/chain-rpc/status",
    "gonka1": "https://node1.gonka.ai:8443/chain-rpc/status",
    "gonka3": "https://node3.gonka.ai/chain-rpc/status",
    "gonka4": "https://node4.gonka.ai/chain-rpc/status",
    "participants": "https://node2.gonka.ai:8443/v1/epochs/current/participants",
    "dex": "https://api.dexscreener.com/token-pairs/v1/ethereum/0x972a7a92d92796a98801a8818bcf91f1648f2f68",
    "ethereum": "https://ethereum-rpc.publicnode.com",
    "llama": "https://eth.llamarpc.com",
    "drpc": "https://eth.drpc.org",
}


def probe(item):
    name, url = item
    payload = None
    if name in {"ethereum", "llama", "drpc"}:
        payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_blockNumber", "params": []}).encode()
    request = urllib.request.Request(url, data=payload, headers={"User-Agent": "GonkaFlow/0.1", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.load(response)
        return {"name": name, "url": url, "ok": True, "data": data}
    except Exception as exc:
        return {"name": name, "url": url, "ok": False, "error": str(exc)}


if __name__ == "__main__":
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        for result in pool.map(probe, TARGETS.items()):
            print(json.dumps(result, ensure_ascii=False)[:14000])
