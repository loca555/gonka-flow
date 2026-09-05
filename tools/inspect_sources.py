"""Capture small public fixtures for integration development, never secrets."""
import concurrent.futures
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1] / "data" / "source-samples"
TOKEN = "0x972a7a92d92796a98801a8818bcf91f1648f2f68"
NODE = "https://node1.gonka.ai:8443"

def read(url, payload=None):
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "GonkaFlow/0.1", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)

def capture(item):
    name, url, payload = item
    try:
        data = read(url, payload)
        ROOT.mkdir(parents=True, exist_ok=True)
        (ROOT / (name + ".json")).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(name, json.dumps(data, ensure_ascii=False)[:2800])
    except Exception as e:
        print(name, str(e))

if __name__ == "__main__":
    rpc = NODE + "/chain-rpc"
    api = NODE + "/chain-api"
    h = int(read(rpc + "/status")["result"]["sync_info"]["latest_block_height"])
    targets = [("block", rpc + f"/block?height={h-2}", None), ("results", rpc + f"/block_results?height={h-2}", None)]
    paths = {
        "epoch": "/productscience/inference/inference/get_current_epoch",
        "tokenomics": "/productscience/inference/inference/tokenomics_data",
        "participants": "/productscience/inference/inference/participant?pagination.limit=1000",
        "settlements": "/productscience/inference/inference/settle_amount?pagination.limit=200",
        "modules": "/cosmos/auth/v1beta1/module_accounts",
        "supply": "/cosmos/bank/v1beta1/supply/by_denom?denom=ngonka",
        "bridge_transactions": "/productscience/inference/inference/bridge_transactions?pagination.limit=10&pagination.reverse=true",
        "bridge_addresses": "/productscience/inference/inference/bridge_addresses/ethereum",
    }
    targets += [(name, api + p, None) for name,p in paths.items()]
    targets += [("active", NODE+"/v1/epochs/current/participants", None), ("dex", "https://api.dexscreener.com/token-pairs/v1/ethereum/"+TOKEN, None)]
    for name, action in [("claim", "MsgClaimRewards"), ("bridge_mint", "MsgRequestBridgeMint"), ("send", "MsgSend")]:
        ns = "cosmos.bank.v1beta1" if action == "MsgSend" else "inference.inference"
        query = urllib.parse.urlencode({"query": f'"message.action=\'/{ns}.{action}\'"', "order_by": '"desc"', "per_page": "2"})
        targets.append((name, rpc+"/tx_search?"+query, None))
    targets.append(("abi", "https://repo.sourcify.dev/contracts/partial_match/1/"+TOKEN+"/metadata.json", None))
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as p:
        list(p.map(capture, targets))
