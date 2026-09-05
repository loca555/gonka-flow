import base64
import hashlib
import json
import re
from datetime import datetime
from Crypto.Hash import keccak
from .config import TOKEN, ZERO, ESCROW

def kh(text):
    h = keccak.new(digest_bits=256)
    h.update(text.encode())
    return "0x" + h.hexdigest()

TRANSFER = kh("Transfer(address,address,uint256)")
SWAP = kh("Swap(address,address,int256,int256,uint160,uint128,int24)")
MINT = kh("WGNKMinted(uint64,bytes32,address,uint256)")
BURN = kh("WGNKBurned(address,uint256,uint256)")
LP_MINT = kh("Mint(address,address,int24,int24,uint128,uint256,uint256)")
LP_BURN = kh("Burn(address,int24,int24,uint128,uint256,uint256)")

def stamp(text):
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())

def attr(event):
    return {a["key"]: a.get("value", "") for a in event.get("attributes", [])}

def amount(text):
    return sum(int(m) for m in re.findall(r"(?:^|,)(\d+)ngonka(?:,|$)", text or ""))

def pb(data):
    """Minimal bounded protobuf wire reader, only for public TxRaw/TxBody/Any."""
    pos, result = 0, {}
    def varint():
        nonlocal pos
        v = shift = 0
        while pos < len(data) and shift <= 63:
            b = data[pos]; pos += 1
            v |= (b & 127) << shift
            if not b & 128:
                return v
            shift += 7
        raise ValueError("Invalid protobuf varint")
    while pos < len(data):
        tag = varint(); field, wire = tag >> 3, tag & 7
        if not field:
            raise ValueError("Invalid protobuf field")
        if wire == 0:
            value = varint()
        elif wire in (1, 2, 5):
            length = varint() if wire == 2 else (8 if wire == 1 else 4)
            if pos + length > len(data):
                raise ValueError("Truncated protobuf")
            value = data[pos:pos + length]; pos += length
        else:
            raise ValueError("Unsupported protobuf wire type")
        result.setdefault(field, []).append(value)
    return result

def messages(raw):
    raw = pb(raw)
    body = pb(raw[1][0])
    result = []
    pending = [(data, 0) for data in body.get(1, [])]
    while pending:
        data, depth = pending.pop(0)
        if depth > 8:
            raise ValueError("Excessive authz nesting")
        anymsg = pb(data)
        name = anymsg[1][0].decode()
        if name == "/cosmos.authz.v1beta1.MsgExec":
            nested = pb(anymsg[2][0])
            pending.extend((item, depth + 1) for item in nested.get(2, []))
        elif name.endswith("MsgBridgeExchange"):
            v = pb(anymsg[2][0])
            fields = {i: v.get(i, [b""])[0].decode() for i in range(1, 10)}
            result.append({"type": name, "origin": fields[2], "contract": fields[3].lower(),
                           "owner": fields[4], "amount": fields[6], "eth_height": fields[7],
                           "receipt_index": fields[8]})
        else:
            result.append({"type": name})
    return result

def blank(chain, block, tx, idx, kind, raw, src="", dst="", **kw):
    e = {"id": f"{chain}:{tx}:{idx}", "chain": chain, "height": block["height"],
         "block_hash": block["hash"], "ts": block["ts"], "tx_hash": tx, "idx": idx,
         "kind": kind, "src": src.lower(), "dst": dst.lower(), "actor": "",
         "amount_raw": str(raw), "asset": "GNK" if chain == "gonka" else "WGNK",
         "quote_raw": "0", "quote_asset": "", "pool": "", "finalized": 1,
         "request_key": "", "meta": {}}
    e.update(kw)
    return e

def parse_native(block_response, result_response, module_names):
    b = block_response["result"]["block"]
    block = {"height": int(b["header"]["height"]), "hash": block_response["result"]["block_id"]["hash"],
             "ts": stamp(b["header"]["time"])}
    if b["header"]["chain_id"] != "gonka-mainnet":
        raise ValueError("Unexpected Gonka chain ID")
    result = result_response["result"]
    if int(result["height"]) != block["height"]:
        raise ValueError("Block/results height mismatch")
    txs = b["data"].get("txs") or []
    outcomes = result.get("txs_results") or []
    if len(txs) != len(outcomes):
        raise ValueError("Missing transaction results")
    groups = []
    for encoded, out in zip(txs, outcomes):
        if int(out.get("code", 0)) != 0:
            continue
        raw = base64.b64decode(encoded, validate=True)
        groups.append((hashlib.sha256(raw).hexdigest().upper(), out.get("events", []), messages(raw)))
    groups.append(("block:" + block["hash"], result.get("finalize_block_events") or [], []))
    events = []
    for tx, raw_events, msgs in groups:
        locks = [attr(x) for x in raw_events if x["type"] == "bridge_mint_requested"]
        actions = [attr(x).get("action", "") for x in raw_events if x["type"] == "message"]
        action_events = [attr(x) for x in raw_events if x["type"]=="message" and attr(x).get("action")]
        vested = any(x["type"] == "vest_reward" for x in raw_events)
        reward_claim = any(a.endswith("MsgClaimRewards") for a in actions)
        bridge_msgs = [x for x in msgs if x["type"].endswith("MsgBridgeExchange")]
        for idx, ev in enumerate(raw_events):
            a = attr(ev); raw_amount = amount(a.get("amount", ""))
            reward_claim = any(x.get("action","").endswith("MsgClaimRewards")
                               and x.get("msg_index","")==a.get("msg_index","") for x in action_events)
            if ev["type"] == "transfer" and raw_amount:
                src, dst = a.get("sender", "").lower(), a.get("recipient", "").lower()
                kind, meta, key = "transfer", {"event": ev["type"], "attributes": a}, ""
                source_module, target_module = module_names.get(src, ""), module_names.get(dst, "")
                lock = next((x for x in locks if x.get("user", "").lower() == src
                             and str(raw_amount) == x.get("amount")
                             and x.get("destination_bridge_address", "").lower() == TOKEN
                             and x.get("chain_id") == "ethereum"
                             and x.get("msg_index", "") == a.get("msg_index", "")), None)
                if dst == ESCROW:
                    kind = "bridge_lock" if lock else "escrow_deposit"
                    if lock:
                        key = kh(lock["request_id"])
                        meta.update(destination=lock["destination_address"].lower(), request=lock["request_id"],
                                    epoch=lock.get("epoch_index"), evidence="bridge_mint_requested")
                elif src == ESCROW:
                    kind = "bridge_release"
                    match = [m for m in bridge_msgs if m["contract"] == TOKEN and m["origin"] == "ethereum"
                             and m["amount"] == str(raw_amount)]
                    if len(match) == 1:
                        meta.update(match[0])
                        key = f"ethereum:{match[0]['eth_height']}:{match[0]['receipt_index']}"
                elif source_module == "streamvesting":
                    kind = "vesting_unlock"
                elif target_module == "streamvesting" and vested:
                    kind = "vesting_funding"
                elif reward_claim and source_module == "inference":
                    kind = "reward_paid"
                    meta["attribution"] = "ClaimRewards recipient; may differ from host address"
                elif target_module == "fee_collector":
                    kind = "fee"
                elif target_module == "collateral":
                    kind = "collateral_deposit"
                elif source_module == "collateral":
                    kind = "collateral_release"
                elif source_module or target_module:
                    kind = "module_transfer"
                events.append(blank("gonka", block, tx, idx, kind, raw_amount, src, dst,
                                    request_key=key, meta=meta))
            elif ev["type"] == "vest_reward" and raw_amount:
                # Can also be a user transfer-with-vesting, not automatically mining.
                kind = "reward_vested" if reward_claim else "vesting_credit"
                events.append(blank("gonka", block, tx, idx, kind, raw_amount,
                                    dst=a.get("participant", ""), meta={"attributes": a, "event": ev["type"]}))
    return block, events

def address(word):
    return "0x" + word[-40:].lower()

def words(data):
    data = data.removeprefix("0x")
    if len(data) % 64:
        raise ValueError("Invalid ABI payload")
    return [int(data[i:i+64], 16) for i in range(0, len(data), 64)]

def signed(v):
    return v - 2**256 if v >= 2**255 else v

def parse_eth_log(log, block, pools, receipt=None, finalized=True):
    topics = [t.lower() for t in log["topics"]]
    if not topics or log.get("removed"):
        return None
    contract, topic = log["address"].lower(), topics[0]
    w = words(log["data"])
    tx = log["transactionHash"].lower()
    idx = int(log["logIndex"], 16)
    common = {"finalized": int(finalized), "meta": {
        "log_index": idx, "transaction_index": int(log["transactionIndex"], 16),
        "contract": contract, "topic": topic}}
    if contract == TOKEN:
        if topic == MINT:
            common["request_key"] = topics[2]
            common["meta"]["epoch"] = int(topics[1], 16)
            return blank("ethereum", block, tx, idx, "bridge_mint", w[0], ZERO, address(topics[3]), **common)
        if topic == BURN:
            common["request_key"] = f"ethereum:{block['height']}:{int(log['transactionIndex'], 16)}"
            return blank("ethereum", block, tx, idx, "bridge_burn", w[0], address(topics[1]), ZERO, **common)
        if topic == TRANSFER:
            src, dst = address(topics[1]), address(topics[2])
            if src == ZERO or dst == ZERO:
                return None  # Same monetary movement has the explicit bridge event.
            return blank("ethereum", block, tx, idx, "transfer", w[0], src, dst, **common)
        return None
    pool = pools.get(contract)
    if not pool:
        return None
    zero = pool["token0"] == TOKEN
    common["pool"] = contract
    common["meta"]["quote_decimals"] = pool["quote_decimals"]
    common["quote_asset"] = pool["quote_symbol"]
    if topic == SWAP:
        a0, a1 = signed(w[0]), signed(w[1])
        token_amount, quote_amount = (a0, a1) if zero else (a1, a0)
        if token_amount == 0 or quote_amount == 0 or token_amount * quote_amount >= 0:
            raise ValueError("Non-opposing V3 swap amounts")
        kind = "sell" if token_amount > 0 else "buy"
        common["quote_raw"] = str(abs(quote_amount))
        common["meta"].update(sender=address(topics[1]), recipient=address(topics[2]),
                              attribution="pool_only")
        if receipt:
            origin = receipt["from"].lower()
            net = 0
            for item in receipt["logs"]:
                t = [x.lower() for x in item["topics"]]
                if item["address"].lower() == TOKEN and t and t[0] == TRANSFER:
                    qty = words(item["data"])[0]
                    net += qty * ((address(t[2]) == origin) - (address(t[1]) == origin))
            common["actor"] = origin
            common["meta"].update(initiator=origin, initiator_net_raw=str(net),
                                  attribution="initiator_net" if ((kind == "buy" and net > 0)
                                  or (kind == "sell" and net < 0)) else "initiator_only")
        return blank("ethereum", block, tx, idx, kind, abs(token_amount),
                     src=address(topics[1]), dst=address(topics[2]), **common)
    if topic in (LP_MINT, LP_BURN):
        # Mint data: sender, liquidity, amount0, amount1; Burn: liquidity, amount0, amount1.
        quantities = w[2:4] if topic == LP_MINT else w[1:3]
        qty, quote = quantities if zero else quantities[::-1]
        common["quote_raw"] = str(quote)
        common["actor"] = address(topics[1])
        return blank("ethereum", block, tx, idx, "liquidity_add" if topic == LP_MINT else "liquidity_remove",
                     qty, **common)
    return None
