import asyncio
import logging
import time
import math
from .codec import parse_native, parse_eth_log, TRANSFER, SWAP, MINT, BURN, LP_MINT, LP_BURN
from .config import TOKEN, ESCROW, SEED_POOLS
from .sources import Sources
from .holders import HolderCollector, GNK_ADDRESS
from .history import timestamp, native_header, boundary_proof

log = logging.getLogger("gonka-flow")
INF = "/productscience/inference/inference"

class Indexer:
    def __init__(self, settings, db):
        self.cfg, self.db = settings, db
        self.net = Sources(settings)
        self.tasks = []
        self.module_names = {}
        self.pools = self.db.get("verified_pools", {})
        self.eth_cache = {}
        self.native_ready, self.eth_ready = asyncio.Event(), asyncio.Event()
        self.history_verified = set()
        self.native_archive_batched = False
        self.holders = HolderCollector(self)
        self.db.put("history_request", {"iso": settings.history_from, "since": timestamp(settings.history_from)}
                    if settings.history_from else None)
        if settings.history_from:
            # Reuse the already verified immutable date boundary across restarts, never for a changed request.
            since = timestamp(settings.history_from)
            for chain, height in (("gonka", settings.gonka_start), ("ethereum", settings.eth_start)):
                proof = self.db.get("history_boundary:" + chain)
                if (proof and proof.get("since") == since and proof.get("first", {}).get("height") == height
                        and proof["first"].get("hash") and proof["first"].get("ts", 0) >= since
                        and (height == 1 or (proof.get("previous", {}).get("height") == height-1
                             and proof["previous"].get("ts", since) < since))):
                    self.history_verified.add(chain)

    def status(self, name, **values):
        previous = self.db.get("status:" + name, {})
        previous.update(values)
        self.db.put("status:" + name, previous)

    async def loop(self, name, function, interval):
        failures = 0
        while True:
            try:
                delay = await function()
                failures = 0
                self.status(name, ok=True, error=None, retry_at=None, rpc_category=None, checked_at=int(time.time()))
                await asyncio.sleep(interval if delay is None else delay)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                failures += 1
                log.warning("%s: %s", name, str(e)[:240])
                delay = max(min(120, max(interval,8) * 2**min(failures-1,4)), getattr(e, "retry_after", 0))
                self.status(name, ok=False, error=str(e)[:240], attempted_at=int(time.time()),
                            retry_at=int(time.time())+math.ceil(delay), rpc_category=getattr(e, "category", None))
                await asyncio.sleep(delay)

    def start(self):
        for name, fn, delay in [
            ("gonka", self.native_tail, self.cfg.poll_seconds),
            ("gonka_history", self.native_history, 1),
            ("ethereum", self.eth_tail, max(12, self.cfg.poll_seconds)),
            ("ethereum_history", self.eth_history, 2),
            ("network", self.network_snapshot, 60),
            ("market", self.market_snapshot, 120),
            ("bridge", self.bridge_receipts, 45),
            ("gnk_holders", self.holders.native, 10),
            ("gnk_holder_updates", self.holders.native_updates, 20),
            ("wgnk_holders", self.holders.ethereum, 20),
        ]:
            self.tasks.append(asyncio.create_task(self.loop(name, fn, delay), name=name))

    async def stop(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.net.close()

    async def native_setup(self, head):
        modules = await self.net.api("/cosmos/auth/v1beta1/module_accounts")
        for account in modules["accounts"]:
            self.module_names[account["base_account"]["address"].lower()] = account["name"]
        if self.module_names.get(ESCROW) != "bridge_escrow":
            raise ValueError("Unexpected bridge escrow module address")
        with self.db.conn:
            for addr, name in self.module_names.items():
                self.db.label(addr, name, "module", "cosmos.auth.module_accounts")
        self.db.snapshot("modules", self.module_names)
        origin = self.db.get("gonka_origin", max(1, head - 12))
        self.db.put("gonka_origin", origin)
        target = self.cfg.gonka_start or max(1, origin - self.cfg.lookback_hours * 680)
        self.db.put("gonka_target", min(target, self.db.get("gonka_target", target)))
        if self.db.get("gonka_next") is None:
            self.db.put("gonka_next", origin)
            self.db.put("gonka_back", origin - 1)
        self.native_ready.set()

    async def native_batch(self, lo, hi, *, archive=False):
        def decode(b, r, height):
            block, events = parse_native(b, r, self.module_names)
            if block["height"] != height:
                raise ValueError("Gonka RPC returned an unexpected height")
            return block, events
        async def one(height):
            b, r = await asyncio.gather(
                self.net.native("/chain-rpc/block", {"height": str(height)}),
                self.net.native("/chain-rpc/block_results", {"height": str(height)}))
            return decode(b, r, height)
        # Validate the entire batch before committing its cursor or any coverage.
        pairs = await self.net.native_blocks(lo, hi) if archive else None
        if archive:
            self.native_archive_batched = pairs is not None
        if pairs is None:
            results = await asyncio.gather(*(one(h) for h in range(lo, hi + 1)))
        else:
            results = [decode(b, r, h) for h, (b, r) in zip(range(lo, hi+1), pairs)]
        blocks = [x[0] for x in results]
        events = [e for _, batch in results for e in batch]
        self.db.save_batch("gonka", lo, hi, events, blocks)

    async def native_tail(self):
        status = await self.net.rpc("status")
        if status["node_info"]["network"] != "gonka-mainnet":
            raise ValueError("Unexpected native chain")
        head = int(status["sync_info"]["latest_block_height"])
        if not self.native_ready.is_set():
            await self.native_setup(head)
        self.status("gonka", remote_height=head, remote_time=status["sync_info"]["latest_block_time"],
                    provider=self.net.current.get("gonka"))
        start = self.db.get("gonka_next")
        if start <= head:
            end = min(head, start + self.cfg.native_batch - 1)
            await self.native_batch(start, end)
            touched=self.db.conn.execute("""
                SELECT src address,MAX(height) h FROM events WHERE chain='gonka' AND height BETWEEN ? AND ? GROUP BY src
                UNION ALL SELECT dst,MAX(height) FROM events WHERE chain='gonka' AND height BETWEEN ? AND ? GROUP BY dst
                """,(start,end,start,end)).fetchall()
            with self.db.conn:
                for row in touched:
                    if GNK_ADDRESS.fullmatch(row["address"]):
                        self.db.conn.execute("""INSERT INTO holder_dirty VALUES(?,?)
                            ON CONFLICT(address) DO UPDATE SET height=MAX(height,excluded.height)""",
                            (row["address"],row["h"]))
            self.db.put("gonka_next", end + 1)
            self.status("gonka", indexed_height=end)
            return .1 if end < head else self.cfg.poll_seconds

    async def native_history(self):
        await self.native_ready.wait()
        # Archive verification must never prevent the live feed from starting.
        if self.cfg.history_from and "gonka" not in self.history_verified:
            async def header(h):
                return native_header(await self.net.native("/chain-rpc/block", {"height": str(h)}), h)
            proof = await boundary_proof(header, self.cfg.gonka_start, timestamp(self.cfg.history_from))
            self.db.put("history_boundary:gonka", proof)
            self.history_verified.add("gonka")
        end, target = self.db.get("gonka_back"), self.db.get("gonka_target")
        proof = self.db.get("history_boundary:gonka") if self.cfg.history_from else None
        if proof and self.db.get("history_boundary_seed") != proof["first"]["height"]:
            first = proof["first"]["height"]
            # A small real June sample validates the decoder, without claiming the intervening history.
            head = self.db.get("status:gonka", {}).get("remote_height", first)
            await self.native_batch(first, min(head, first + self.cfg.native_batch - 1))
            self.db.put("history_boundary_seed", first)
        # Prioritise the current epoch boundary: unlocks would otherwise take hours to appear.
        active = (self.db.get("hosts") or {}).get("data",{})
        epoch = active.get("epoch_id")
        boundary = int(active.get("effective_block_height",0))
        if epoch and self.db.get("epoch_boundary_seed") != epoch and boundary-2 >= target:
            await self.native_batch(boundary-2,boundary+1)
            self.db.put("epoch_boundary_seed",epoch)
        self.status("gonka_history", target=target, remaining=max(0, end - target + 1))
        if end < target:
            return 30
        start = max(target, end - self.cfg.native_history_batch + 1)
        await self.native_batch(start, end, archive=True)
        self.db.put("gonka_back", start - 1)
        self.status("gonka_history", remaining=max(0, start-target),
                    provider=self.net.current.get("gonka_archive") if self.native_archive_batched else None,
                    batch_blocks=end-start+1 if self.native_archive_batched else None)
        return .15

    async def eth_setup(self, finalized):
        decimals = int(await self.net.call(TOKEN, "decimals()"), 16)
        if decimals != 9:
            raise ValueError("Unexpected WGNK decimals")
        verified = {}
        for addr in SEED_POOLS:
            verified[addr] = await self.net.pool(addr)
        self.pools = verified
        self.db.put("verified_pools", verified)
        with self.db.conn:
            self.db.label(TOKEN, "WGNK · официальный мост", "bridge", "gonka.ai/docs")
            for addr in verified:
                self.db.label(addr, "Uniswap V3 · WGNK / " + verified[addr]["quote_symbol"],
                              "pool", "UniswapV3Factory.getPool")
        origin = self.db.get("ethereum_origin", max(1, finalized - 100))
        self.db.put("ethereum_origin", origin)
        target = self.cfg.eth_start or max(1, origin - self.cfg.lookback_hours * 300)
        self.db.put("ethereum_target", min(target, self.db.get("ethereum_target", target)))
        if self.db.get("ethereum_next") is None:
            self.db.put("ethereum_next", origin)
            self.db.put("ethereum_back", origin - 1)
        self.eth_ready.set()

    async def eth_block(self, height, cached=True, *, archive=False):
        if cached and height in self.eth_cache:
            return self.eth_cache[height]
        b = await self.net.eth("eth_getBlockByNumber", [hex(height), False], archive=archive)
        if int(b["number"], 16) != height:
            raise ValueError("Ethereum block number mismatch")
        result = {"height": height, "hash": b["hash"].lower(), "ts": int(b["timestamp"], 16)}
        if cached:
            if len(self.eth_cache) > 2500:
                self.eth_cache.clear()
            self.eth_cache[height] = result
        return result

    async def eth_logs(self, lo, hi, *, archive=False):
        return await self.net.eth_logs({
            "fromBlock": hex(lo), "toBlock": hex(hi),
            "address": [TOKEN] + list(self.pools),
            "topics": [[TRANSFER, SWAP, MINT, BURN, LP_MINT, LP_BURN]]}, archive=archive)

    async def eth_batch(self, lo, hi, finalized=True, *, archive=False):
        if lo > hi:
            return
        logs = await self.eth_logs(lo, hi, archive=archive)
        heights = sorted({lo, hi} | {int(x["blockNumber"], 16) for x in logs})
        blocks=[]
        # Do not queue an archive batch ahead of every live RPC request.
        batch_size = 2 if archive else 8
        for pos in range(0,len(heights),batch_size):
            blocks.extend(await asyncio.gather(*(self.eth_block(h,cached=finalized,archive=archive)
                                                for h in heights[pos:pos+batch_size])))
        by_height = {b["height"]: b for b in blocks}
        hashes = {x["transactionHash"] for x in logs if x["topics"][0].lower() == SWAP
                  and x["address"].lower() in self.pools}
        hashes=list(hashes)
        receipts=[]
        for pos in range(0,len(hashes),batch_size):
            receipts.extend(await asyncio.gather(*(self.net.eth("eth_getTransactionReceipt",[h],archive=archive)
                                                  for h in hashes[pos:pos+batch_size])))
        by_tx = dict(zip(hashes, receipts))
        events = []
        for item in logs:
            if not lo<=int(item["blockNumber"],16)<=hi:
                raise ValueError("Ethereum log outside requested range")
            b = by_height[int(item["blockNumber"], 16)]
            if item["blockHash"].lower() != b["hash"]:
                raise ValueError("Ethereum reorg during batch; retrying without checkpoint")
            receipt = by_tx.get(item["transactionHash"])
            if receipt and (receipt["blockHash"].lower() != b["hash"] or int(receipt["status"], 16) != 1):
                raise ValueError("Swap receipt inconsistent with canonical block")
            e = parse_eth_log(item, b, self.pools, receipt, finalized)
            if e:
                events.append(e)
        self.db.save_batch("ethereum", lo, hi, events, blocks if finalized else [],
                           finalized=finalized, replace_pending=not finalized)

    async def eth_tail(self):
        final, latest = await asyncio.gather(
            self.net.eth("eth_getBlockByNumber", ["finalized", False]),
            self.net.eth("eth_getBlockByNumber", ["latest", False]))
        finalized, head = int(final["number"], 16), int(latest["number"], 16)
        if not self.eth_ready.is_set():
            await self.eth_setup(finalized)
        self.status("ethereum", remote_height=head, finalized_height=finalized,
                    provider=self.net.current.get("ethereum"), remote_timestamp=int(latest["timestamp"], 16))
        start = self.db.get("ethereum_next")
        if start <= finalized:
            end = min(finalized, start + 499)
            await self.eth_batch(start, end)
            self.db.put("ethereum_next", end + 1)
            self.status("ethereum", indexed_height=end)
        # Replay the entire unfinalized suffix. Never accumulate orphaned provisional swaps.
        if head - finalized > 400:
            raise ValueError("Ethereum finality lag >400 blocks; provisional feed paused")
        await self.eth_batch(finalized + 1, head, finalized=False)
        self.status("ethereum", provisional_height=head)
        return .5 if self.db.get("ethereum_next") <= finalized else max(12, self.cfg.poll_seconds)

    async def eth_history(self):
        await self.eth_ready.wait()
        if self.cfg.history_from and "ethereum" not in self.history_verified:
            async def header(h):
                return await self.eth_block(h, archive=True)
            proof = await boundary_proof(header, self.cfg.eth_start, timestamp(self.cfg.history_from))
            self.db.put("history_boundary:ethereum", proof)
            self.history_verified.add("ethereum")
        end, target = self.db.get("ethereum_back"), self.db.get("ethereum_target")
        self.status("ethereum_history", target=target, remaining=max(0, end - target + 1))
        if end < target:
            return 30
        start = max(target, end - self.cfg.eth_history_batch + 1)
        await self.eth_batch(start, end, archive=True)
        self.db.put("ethereum_back", start - 1)
        return .5

    async def network_snapshot(self):
        await self.native_ready.wait()
        active, supply, tokenomics, escrow = await asyncio.gather(
            self.net.native("/v1/epochs/current/participants"),
            self.net.api("/cosmos/bank/v1beta1/supply/by_denom", {"denom": "ngonka"}),
            self.net.api(INF + "/tokenomics_data"),
            self.net.api("/cosmos/bank/v1beta1/balances/" + ESCROW + "/by_denom", {"denom": "ngonka"}))
        self.db.snapshot("hosts", active["active_participants"])
        self.db.snapshot("supply", supply["amount"])
        self.db.snapshot("tokenomics", tokenomics["tokenomics_data"])
        self.db.snapshot("escrow", escrow["balance"])
        # Current epoch actors are not permanent or verified real-world identities.
        with self.db.conn:
            self.db.conn.execute("DELETE FROM labels WHERE role='host'")
            for p in active["active_participants"].get("participants", []):
                self.db.label(p["index"], "Хост · эпоха " + active["active_participants"]["epoch_id"],
                              "host", "Gonka active_participants")
        if self.eth_ready.is_set():
            supply = await self.net.call(TOKEN, "totalSupply()")
            self.db.snapshot("wgnk_supply", {"amount": str(int(supply, 16))})

    async def market_snapshot(self):
        data = await self.net.get("https://api.dexscreener.com/token-pairs/v1/ethereum/" + TOKEN)
        self.db.snapshot("market", [x for x in data if x.get("chainId") == "ethereum"
                                  and x.get("baseToken", {}).get("address", "").lower() == TOKEN])
        # New pools require explicit validation + history replay; do not silently claim coverage.
        unknown = [x["pairAddress"].lower() for x in data
                   if x.get("chainId") == "ethereum" and x["pairAddress"].lower() not in SEED_POOLS]
        self.db.put("undecoded_pools", unknown)

    async def bridge_receipts(self):
        await self.eth_ready.wait()
        rows = self.db.conn.execute("""
            SELECT e.* FROM events e LEFT JOIN bridge_receipts b ON b.event_id=e.id
            WHERE e.kind='bridge_burn' AND e.finalized=1
            AND (b.event_id IS NULL OR (b.checked_at<? AND json_extract(b.value,'$.status')!='completed'))
            ORDER BY e.ts DESC LIMIT 15""", (int(time.time()) - 180,)).fetchall()
        for row in rows:
            e = self.db.event(row)
            index = e["meta"]["transaction_index"]
            data = await self.net.api(INF + f"/bridge_transaction/ethereum/{e['height']}/{index}")
            matches = [x for x in data.get("bridgeTransactions", [])
                       if x.get("contractAddress", "").lower() == TOKEN
                       and x.get("chainId") == "ethereum"
                       and x.get("status") == "BRIDGE_COMPLETED"
                       and str(x.get("amount")) == e["amount_raw"]
                       and int(x.get("blockNumber", -1)) == e["height"]
                       and int(x.get("receiptIndex", -1)) == index]
            value = {"status": "completed" if len(matches) == 1 else "unmatched",
                     "evidence": "Gonka bridge_transaction(ethereum,block,receipt)",
                     "record": matches[0] if len(matches) == 1 else None}
            import json
            with self.db.conn:
                self.db.conn.execute("INSERT OR REPLACE INTO bridge_receipts VALUES(?,?,?)",
                                     (e["id"], json.dumps(value), int(time.time())))
            self.db.revision += 1
