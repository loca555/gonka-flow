import asyncio
import time
import re
import math
from urllib.parse import urlsplit
import httpx
from .codec import kh
from .config import TOKEN, USDT, FACTORY
from .eth_rpc import EthereumRPCError, rpc_result

class Sources:
    def __init__(self, settings):
        self.settings = settings
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(25, connect=8),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=12),
            headers={"User-Agent": "GonkaFlow/0.1 (read-only public analytics)"})
        self.native_slots = asyncio.Semaphore(10)
        self.eth_slots = asyncio.Semaphore(4)
        self.eth_archive_slots = asyncio.Semaphore(2)
        self.current = {}
        self.verified_eth = set()
        self.native_next = {}
        self.native_cooldown = {}
        self.native_floor = {}
        self.native_batch_disabled = {}
        self.eth_next = {}
        self.eth_cooldown = {}
        self.eth_lane_cooldown = {}
        self.eth_method_cooldown = {}
        self.eth_locks = {}
        self.eth_failures = {}
        self.eth_intervals = {}
        self.eth_successes = {}

    async def close(self):
        await self.client.aclose()

    async def get(self, url, params=None, headers=None, with_height=False):
        response = await self.client.get(url, params=params, headers=headers)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and data.get("error"):
            raise ValueError(str(data["error"])[:200])
        if with_height:
            height = response.headers.get("x-cosmos-block-height",
                         response.headers.get("grpc-metadata-x-cosmos-block-height"))
            if not height or not height.isdigit():
                raise ValueError("Missing Cosmos snapshot block height")
            return data, int(height)
        return data

    async def native(self, path, params=None, *, height=None, with_height=False):
        errors = []
        is_rpc = path.startswith("/chain-rpc/")
        endpoints = [(base, path, 3.2) for base in self.settings.gonka]
        if is_rpc:
            endpoints = [(base, path.removeprefix("/chain-rpc"), .5)
                         for base in self.settings.gonka_rpc] + endpoints
        async with self.native_slots:
            requested = int((params or {}).get("height", 0)) if is_rpc else 0
            # Once a provider proves pruning, do not ask it for every older block.
            endpoints = [e for e in endpoints if not requested or requested >= self.native_floor.get(e[0], 0)]
            if requested and not any(e[0] in self.settings.gonka_rpc for e in endpoints):
                # Archive fallbacks share work at their existing per-host rate; live RPC stays separate.
                endpoints.sort(key=lambda e: self.native_next.get(e[0], 0))
            for base, routed_path, interval in endpoints:
                if self.native_cooldown.get(base,0) > time.monotonic():
                    errors.append(urlsplit(base).netloc + " cooldown")
                    continue
                now = time.monotonic()
                slot = max(now, self.native_next.get(base,now))
                self.native_next[base] = slot + interval
                await asyncio.sleep(max(0,slot-now))
                if self.native_cooldown.get(base, 0) > time.monotonic():
                    errors.append((urlsplit(base).hostname or "RPC") + " cooldown")
                    continue
                try:
                    try:
                        if base in self.settings.gonka_rpc and path in ("/chain-rpc/block","/chain-rpc/block_results"):
                            response = await self.client.post(base+"/",json={
                                "jsonrpc":"2.0","id":1,"method":path.rsplit("/",1)[-1],"params":params or {}})
                            response.raise_for_status()
                            data = response.json()
                            if data.get("error"):
                                raise ValueError(str(data["error"])[:180])
                        else:
                            data = await self.get(base + routed_path, params,
                                {"x-cosmos-block-height":str(height)} if height is not None else None,
                                with_height=with_height)
                            if with_height and height is not None and data[1] != height:
                                raise ValueError("Cosmos provider ignored pinned snapshot height")
                    except httpx.HTTPStatusError as err:
                        if not is_rpc or err.response.status_code not in (404,405):
                            raise
                        rpc_root = base + ("/" if base in self.settings.gonka_rpc else "/chain-rpc/")
                        response = await self.client.post(rpc_root, json={
                            "jsonrpc":"2.0", "id":1, "method":path.rsplit("/",1)[-1],
                            "params":params or {}})
                        response.raise_for_status()
                        data = response.json()
                        if data.get("error"):
                            raise ValueError(str(data["error"])[:180])
                    self.current["gonka"] = urlsplit(base).netloc
                    return data
                except (httpx.HTTPError, ValueError) as e:
                    floor = re.search(r"lowest height is (\d+)", str(e))
                    if requested and floor:
                        self.native_floor[base] = max(self.native_floor.get(base,0), int(floor[1]))
                    if isinstance(e,httpx.HTTPStatusError) and e.response.status_code in (429,503):
                        self.native_cooldown[base] = time.monotonic() + 60
                    errors.append(urlsplit(base).netloc + ": " + type(e).__name__ + (f" ({e.response.status_code})"
                                  if isinstance(e,httpx.HTTPStatusError) else " " + str(e)[:120]))
        raise RuntimeError("Gonka " + path.rsplit("/",1)[-1] + " " + str((params or {}).get("height",""))
                           + " unavailable: " + ", ".join(errors))

    async def native_blocks(self, lo, hi):
        """Batch only read-only block/results; return None when unsupported, never partial data."""
        if lo < 1 or hi < lo or hi-lo >= 24:
            raise ValueError("Native RPC batch must contain 1..24 blocks")
        # Official CometBFT servers advertise a hard cap of 10 methods per JSON-RPC packet.
        if hi-lo >= 5:
            combined = []
            for start in range(lo, hi+1, 5):
                part = await self.native_blocks(start, min(hi, start+4))
                if part is None:
                    return None
                combined.extend(part)
            return combined
        payload = [{"jsonrpc":"2.0", "id":f"{method}:{h}", "method":method, "params":{"height":str(h)}}
                   for h in range(lo, hi+1) for method in ("block", "block_results")]
        endpoints = [(url, url.removesuffix("/chain-rpc")) for url in self.settings.gonka_archive]
        endpoints.sort(key=lambda item: self.native_next.get(item[1], 0))
        expected = {row["id"] for row in payload}
        errors = []
        for endpoint, base in endpoints:
            host = urlsplit(base).hostname or "RPC"
            if self.native_batch_disabled.get(base, 0) > time.monotonic():
                continue
            if lo < self.native_floor.get(base, 0):
                errors.append(host + ": старые блоки удалены")
                continue
            if self.native_cooldown.get(base, 0) > time.monotonic():
                errors.append(host + ": пауза RPC")
                continue
            now = time.monotonic()
            slot = max(now, self.native_next.get(base, now))
            # Shared with REST/single queries: at most one HTTP request per 4 s on this archive host.
            self.native_next[base] = slot + 4
            await asyncio.sleep(max(0, slot-now))
            if self.native_cooldown.get(base, 0) > time.monotonic():
                errors.append(host + ": пауза RPC")
                continue
            try:
                reply = await self.client.post(endpoint + "/", json=payload)
                if reply.status_code in (400, 404, 405, 413):
                    self.native_batch_disabled[base] = time.monotonic() + 3600
                    continue
                reply.raise_for_status()
                rows = reply.json()
                batch_error = rows.get("error") if isinstance(rows, dict) else None
                if isinstance(batch_error, dict) and batch_error.get("code") in (-32600, -32601):
                    self.native_batch_disabled[base] = time.monotonic() + 3600
                    continue
                if not isinstance(rows, list) or len(rows) != len(payload):
                    raise ValueError("Неполный пакет RPC")
                by_id = {row["id"]: row for row in rows}
                if set(by_id) != expected or len(by_id) != len(rows):
                    raise ValueError("Не совпадают ID ответов RPC")
                for row in rows:
                    if row.get("error"):
                        floor = re.search(r"lowest height is (\d+)", str(row["error"]))
                        if floor:
                            self.native_floor[base] = max(self.native_floor.get(base, 0), int(floor[1]))
                        raise ValueError("RPC не вернул один из блоков/результатов пакета")
                    if not isinstance(row.get("result"), dict):
                        raise ValueError("Пустой результат RPC")
                self.current["gonka_archive"] = host
                return [(by_id[f"block:{h}"], by_id[f"block_results:{h}"]) for h in range(lo, hi+1)]
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
                delay = 60
                if isinstance(error, httpx.HTTPStatusError):
                    from .eth_rpc import retry_after
                    delay = max(delay, retry_after(error.response.headers.get("Retry-After")))
                self.native_cooldown[base] = time.monotonic() + delay
                errors.append(host + ": " + type(error).__name__)
        if errors:
            raise RuntimeError("Gonka archive batch unavailable: " + "; ".join(errors))
        # Explicit fallback for endpoints that do not implement JSON-RPC batches.
        return None

    async def rpc(self, method, params=None):
        return (await self.native("/chain-rpc/" + method, params))["result"]

    async def api(self, path, params=None, **kwargs):
        return await self.native("/chain-api" + path, params, **kwargs)

    def eth_wait(self, endpoint, lane, method):
        until = max(self.eth_cooldown.get(endpoint, 0),
                    self.eth_lane_cooldown.get((endpoint, lane), 0),
                    self.eth_method_cooldown.get((endpoint, lane, method), 0))
        return max(0, until - time.monotonic())

    async def eth_post(self, endpoint, method, params):
        # Called under the same endpoint lock in either lane, including chainId verification.
        await asyncio.sleep(max(0, self.eth_next.get(endpoint, 0) - time.monotonic()))
        interval = self.eth_intervals.get(endpoint, self.settings.eth_rpc_interval)
        self.eth_next[endpoint] = time.monotonic() + interval
        response = await self.client.post(endpoint, json={
            "jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        return rpc_result(response)

    def eth_failed(self, endpoint, lane, method, error):
        key = (endpoint, lane)
        failures = self.eth_failures[key] = self.eth_failures.get(key, 0) + 1
        now = time.monotonic()
        delay = max(error.retry_after, min(300, 30 * 2**min(failures-1, 4)))
        if error.category == "range":
            return
        if error.category == "rate_limit":
            delay = max(delay, 45)
            self.eth_cooldown[endpoint] = max(self.eth_cooldown.get(endpoint, 0), now + delay)
            interval = self.eth_intervals.get(endpoint, self.settings.eth_rpc_interval)
            self.eth_intervals[endpoint] = min(max(10, self.settings.eth_rpc_interval), interval * 2)
            self.eth_successes[endpoint] = 0
        elif error.category == "unsupported":
            self.eth_method_cooldown[(endpoint, lane, method)] = now + max(3600, delay)
        elif error.category == "access":
            # A public endpoint can serve live data while refusing historical data.
            self.eth_lane_cooldown[key] = now + max(3600, delay)
        elif error.category == "wrong_chain":
            self.eth_cooldown[endpoint] = now + max(3600, delay)
        else:
            self.eth_lane_cooldown[key] = now + delay

    async def eth(self, method, params, *, archive=False):
        errors, categories, waits = [], [], []
        lane = "archive" if archive else "live"
        endpoints = self.settings.ethereum_archive if archive else self.settings.ethereum
        async with self.eth_archive_slots if archive else self.eth_slots:
            for endpoint in dict.fromkeys(endpoints):
                host = urlsplit(endpoint).hostname or "RPC"
                lock = self.eth_locks.setdefault(endpoint, asyncio.Lock())
                async with lock:
                    # Check after taking the lock: already queued requests also obey a new 429.
                    wait = self.eth_wait(endpoint, lane, method)
                    if wait:
                        errors.append(f"{host}: пауза {math.ceil(wait)} с")
                        waits.append(wait)
                        continue
                    try:
                        if endpoint not in self.verified_eth:
                            chain = await self.eth_post(endpoint, "eth_chainId", [])
                            if int(chain, 16) != 1:
                                raise EthereumRPCError("нужен Ethereum mainnet", "wrong_chain")
                            self.verified_eth.add(endpoint)
                        result = await self.eth_post(endpoint, method, params)
                        self.current["ethereum_archive" if archive else "ethereum"] = host
                        self.eth_failures[(endpoint, lane)] = 0
                        self.eth_successes[endpoint] = self.eth_successes.get(endpoint, 0) + 1
                        if self.eth_successes[endpoint] >= 40:
                            self.eth_successes[endpoint] = 0
                            self.eth_intervals[endpoint] = max(self.settings.eth_rpc_interval,
                                self.eth_intervals.get(endpoint, self.settings.eth_rpc_interval) * .9)
                        return result
                    except (httpx.HTTPError, ValueError, KeyError, TypeError, EthereumRPCError) as problem:
                        error = problem if isinstance(problem, EthereumRPCError) else EthereumRPCError(
                            "ошибка соединения или ответа " + type(problem).__name__)
                        self.eth_failed(endpoint, lane, method, error)
                        errors.append(host + ": " + str(error))
                        categories.append(error.category)
                        waits.append(self.eth_wait(endpoint, lane, method))
        category = "range" if "range" in categories else categories[0] if categories else "cooldown"
        delay = 0 if category == "range" else min(waits, default=30)
        raise EthereumRPCError("Ethereum " + lane + ": " + ("; ".join(errors) or "RPC не настроен"),
                               category, math.ceil(delay))

    async def eth_logs(self, query, *, archive=False):
        try:
            return await self.eth("eth_getLogs", [query], archive=archive)
        except EthereumRPCError as error:
            lo, hi = int(query["fromBlock"], 16), int(query["toBlock"], 16)
            if error.category != "range" or lo >= hi:
                raise
            mid = (lo + hi) // 2
            left = await self.eth_logs({**query, "toBlock": hex(mid)}, archive=archive)
            right = await self.eth_logs({**query, "fromBlock": hex(mid + 1)}, archive=archive)
            return left + right

    async def call(self, contract, signature, args="", tag="latest", *, archive=False):
        return await self.eth("eth_call", [{"to": contract, "data": kh(signature)[:10] + args}, tag], archive=archive)

    async def pool(self, address):
        factory, t0, t1, fee = await asyncio.gather(
            self.call(address, "factory()"), self.call(address, "token0()"),
            self.call(address, "token1()"), self.call(address, "fee()"))
        factory, t0, t1 = ("0x"+v[-40:].lower() for v in (factory, t0, t1))
        fee = int(fee, 16)
        if factory != FACTORY or TOKEN not in (t0, t1):
            raise ValueError("Pool is not canonical Uniswap V3 WGNK")
        verified = await self.call(FACTORY, "getPool(address,address,uint24)",
                                  t0[2:].zfill(64) + t1[2:].zfill(64) + hex(fee)[2:].zfill(64))
        if "0x" + verified[-40:].lower() != address:
            raise ValueError("Factory pool mismatch")
        quote = t1 if t0 == TOKEN else t0
        decimals = int(await self.call(quote, "decimals()"), 16)
        if decimals > 36:
            raise ValueError("Unsupported quote precision")
        return {"address": address, "token0": t0, "token1": t1, "fee": fee,
                "quote": quote, "quote_decimals": decimals,
                "quote_symbol": "USDT" if quote == USDT else quote[:10],
                "verified_at": int(time.time()), "source": "Uniswap V3 factory eth_call"}
