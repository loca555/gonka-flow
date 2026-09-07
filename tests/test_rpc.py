import asyncio
import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock
import httpx
from app.config import Settings
from app.sources import Sources
from app.eth_rpc import EthereumRPCError, retry_after, rpc_result
from app.db import Database
from app.indexer import Indexer


def native_reply(payload):
    rows = []
    for request in payload:
        h = int(request["params"]["height"])
        result = {"height":str(h), "txs_results":None, "finalize_block_events":[]} if request["method"] == "block_results" else {
            "block_id":{"hash":f"hash{h}"}, "block":{"header":{"height":str(h), "chain_id":"gonka-mainnet",
            "time":datetime.fromtimestamp(1780779600+h,timezone.utc).isoformat()}, "data":{"txs":None}}}
        rows.append({"jsonrpc":"2.0", "id":request["id"], "result":result})
    return rows


class ErrorTests(unittest.TestCase):
    def test_retry_after_seconds_date_invalid(self):
        self.assertEqual(retry_after("120"), 120)
        self.assertEqual(retry_after("0.5"), 1)
        self.assertEqual(retry_after("Thu, 01 Jan 1970 00:02:00 GMT", now=60), 60)
        for value in ("bad", "-2", "nan", "inf", None):
            self.assertEqual(retry_after(value), 0)

    def test_http_error_body_is_classified_without_disclosing_secrets(self):
        cases = [
            (403, "Archive requests require a personal token SECRET", "access"),
            (400, "query returned more than 10000 results SECRET", "range"),
            (429, "too many requests SECRET", "rate_limit"),
            (400, "Can't route your request to suitable provider SECRET", "unavailable"),
            (200, "method not found SECRET", "unsupported")]
        for status, message, category in cases:
            with self.subTest(category=category):
                with self.assertRaises(EthereumRPCError) as caught:
                    rpc_result(httpx.Response(status,json={"error":{"code":-32602,"message":message}}))
                self.assertEqual(caught.exception.category, category)
                self.assertNotIn("SECRET",str(caught.exception))

    def test_empty_log_list_is_success_but_missing_result_is_not(self):
        self.assertEqual(rpc_result(httpx.Response(200,json={"result":[]})), [])
        with self.assertRaises(EthereumRPCError):
            rpc_result(httpx.Response(200,json={"result":None}))


class NetworkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.net = Sources(Settings(ethereum=["https://live.test"], ethereum_archive=["https://archive.test"],
            gonka_archive=["https://native.test/chain-rpc"], history_from="", eth_rpc_interval=0))
        await self.net.client.aclose()
    async def asyncTearDown(self):
        await self.net.close()
    def transport(self, handler):
        self.net.client=httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def test_live_archive_are_routed_separately_and_chain_is_verified(self):
        calls=[]
        def handle(request):
            data=json.loads(request.content)
            calls.append((request.url.host, data["method"]))
            return httpx.Response(200,json={"result":"0x1" if data["method"]=="eth_chainId" else []})
        self.transport(handle)
        await self.net.eth("eth_getLogs",[],archive=True)
        await self.net.eth("eth_getLogs",[])
        self.assertEqual(calls,[("archive.test","eth_chainId"),("archive.test","eth_getLogs"),
                                ("live.test","eth_chainId"),("live.test","eth_getLogs")])

    async def test_queued_calls_respect_new_retry_after(self):
        self.net.verified_eth.add("https://archive.test")
        calls=[]
        def handle(request):
            calls.append(request)
            return httpx.Response(429,headers={"Retry-After":"120"},json={"error":{"message":"rate limit"}})
        self.transport(handle)
        replies=await asyncio.gather(*(self.net.eth("eth_getLogs",[],archive=True) for _ in range(6)),
                                     return_exceptions=True)
        self.assertEqual(len(calls),1)
        self.assertTrue(all(isinstance(error,EthereumRPCError) for error in replies))
        self.assertGreaterEqual(replies[0].retry_after,119)
        self.assertGreater(self.net.eth_cooldown["https://archive.test"],time.monotonic()+118)

    async def test_archive_uses_reserve_after_timeout_and_access_error(self):
        endpoints=["https://timeout.test","https://restricted.test","https://reserve.test"]
        self.net.settings.ethereum_archive=endpoints
        self.net.verified_eth.update(endpoints)
        calls=[]
        def handle(request):
            calls.append(request.url.host)
            if request.url.host=="timeout.test":
                raise httpx.ReadTimeout("temporary timeout",request=request)
            if request.url.host=="restricted.test":
                return httpx.Response(403,json={"error":{"message":"personal token required"}})
            return httpx.Response(200,json={"result":[{"logIndex":"0x1"}]})
        self.transport(handle)
        self.assertEqual(await self.net.eth("eth_getLogs",[],archive=True),[{"logIndex":"0x1"}])
        self.assertEqual(calls,["timeout.test","restricted.test","reserve.test"])
        self.assertEqual(self.net.current["ethereum_archive"],"reserve.test")
        calls.clear()
        self.assertEqual(await self.net.eth("eth_getLogs",[],archive=True),[{"logIndex":"0x1"}])
        self.assertEqual(calls,["reserve.test"])

    async def test_archive_access_denial_does_not_disable_same_endpoint_live(self):
        url="https://name:SECRET@live.test/private-SECRET?key=SECRET"
        self.net.settings.ethereum=[url]
        self.net.settings.ethereum_archive=[url]
        self.net.verified_eth.add(url)
        def handle(request):
            data=json.loads(request.content)
            if data["params"]==["old"]:
                return httpx.Response(403,json={"error":{"message":"personal token required SECRET"}})
            return httpx.Response(200,json={"result":"latest"})
        self.transport(handle)
        with self.assertRaises(EthereumRPCError) as caught:
            await self.net.eth("eth_getBlockByNumber",["old"],archive=True)
        self.assertNotIn("SECRET",str(caught.exception))
        self.assertEqual(await self.net.eth("eth_getBlockByNumber",["latest"]),"latest")

    async def test_wrong_chain_never_accepted(self):
        calls=[]
        def handle(request):
            calls.append(json.loads(request.content)["method"])
            return httpx.Response(200,json={"result":"0x89"})
        self.transport(handle)
        with self.assertRaises(EthereumRPCError) as caught:
            await self.net.eth("eth_getLogs",[],archive=True)
        self.assertEqual(caught.exception.category,"wrong_chain")
        self.assertEqual(calls,["eth_chainId"])

    async def test_only_range_errors_split_and_cover_every_block(self):
        calls=[]
        async def reply(method,params,**kwargs):
            q=params[0]
            lo,hi=int(q["fromBlock"],16),int(q["toBlock"],16)
            calls.append((lo,hi,kwargs))
            if hi-lo>=2:
                raise EthereumRPCError("too wide","range")
            return list(range(lo,hi+1))
        self.net.eth=reply
        self.assertEqual(await self.net.eth_logs({"fromBlock":"0x1","toBlock":"0x8"},archive=True),list(range(1,9)))
        self.assertTrue(all(item[2]["archive"] for item in calls))
        for category in ("access","rate_limit","unavailable"):
            self.net.eth=AsyncMock(side_effect=EthereumRPCError("blocked",category))
            with self.assertRaises(EthereumRPCError):
                await self.net.eth_logs({"fromBlock":"0x1","toBlock":"0x8"},archive=True)
            self.net.eth.assert_awaited_once()

    async def test_native_batch_reorders_by_id_and_contains_only_read_methods(self):
        payloads=[]
        def handle(request):
            payload=json.loads(request.content)
            payloads.append(payload)
            return httpx.Response(200,json=list(reversed(native_reply(payload))))
        self.transport(handle)
        pairs=await self.net.native_blocks(10,12)
        self.assertEqual(len(payloads),1)
        self.assertEqual({p["method"] for p in payloads[0]},{"block","block_results"})
        self.assertEqual([p[0]["result"]["block"]["header"]["height"] for p in pairs],["10","11","12"])
        self.assertEqual([p[1]["result"]["height"] for p in pairs],["10","11","12"])

    async def test_native_partial_batch_is_not_accepted(self):
        self.transport(lambda r:httpx.Response(200,json=native_reply(json.loads(r.content))[:-1]))
        with self.assertRaises(RuntimeError):
            await self.net.native_blocks(10,12)

    async def test_large_native_job_is_split_at_server_limit(self):
        sizes=[]
        def handle(request):
            payload=json.loads(request.content)
            sizes.append(len(payload))
            self.net.native_next.clear()  # the transport in this unit test has no real rate limit
            return httpx.Response(200,json=native_reply(payload))
        self.transport(handle)
        pairs=await self.net.native_blocks(10,21)
        self.assertEqual(sizes,[10,10,4])
        self.assertEqual([p[1]["result"]["height"] for p in pairs],[str(h) for h in range(10,22)])

    async def test_native_unsupported_batch_returns_explicit_fallback(self):
        self.transport(lambda r:httpx.Response(200,json={"error":{"code":-32600,"message":"invalid request"}}))
        self.assertIsNone(await self.net.native_blocks(10,12))
        self.assertGreater(self.net.native_batch_disabled["https://native.test"],time.monotonic())

    async def test_native_bad_ids_and_error_rows_are_rejected(self):
        def handle(request):
            rows=native_reply(json.loads(request.content))
            rows[-1]["id"]=rows[0]["id"]
            return httpx.Response(200,json=rows)
        self.transport(handle)
        with self.assertRaises(RuntimeError): await self.net.native_blocks(10,12)
        self.net.native_next.clear()
        self.net.native_cooldown.clear()
        def error_row(request):
            rows=native_reply(json.loads(request.content))
            rows[0]={"id":rows[0]["id"],"error":{"message":"lowest height is 100"}}
            return httpx.Response(200,json=rows)
        self.transport(error_row)
        with self.assertRaises(RuntimeError): await self.net.native_blocks(10,12)
        self.assertEqual(self.net.native_floor["https://native.test"],100)


class ArchiveCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=Database(Path(self.temp.name)/"db.sqlite3")
        self.idx=Indexer(Settings(history_from="",native_history_batch=12,eth_history_batch=1000),self.db)
        await self.idx.net.close()
    async def asyncTearDown(self):
        self.db.close()
        self.temp.cleanup()

    async def test_failed_eth_archive_package_does_not_advance(self):
        self.idx.eth_ready.set()
        self.db.put("ethereum_back",5000)
        self.db.put("ethereum_target",1)
        self.idx.eth_batch=AsyncMock(side_effect=EthereumRPCError("rate limit","rate_limit"))
        with self.assertRaises(EthereumRPCError): await self.idx.eth_history()
        self.idx.eth_batch.assert_awaited_once_with(4001,5000,archive=True)
        self.assertEqual(self.db.get("ethereum_back"),5000)
        self.assertEqual(self.db.coverage("ethereum")["blocks"],0)

    async def test_verified_boundary_reused_only_for_same_requested_date_and_height(self):
        iso="2026-06-07T00:00:00+03:00"
        for chain in ("gonka","ethereum"):
            self.db.put("history_boundary:"+chain,{"since":1780779600,
                "first":{"height":5,"ts":1780779600,"hash":"five"},
                "previous":{"height":4,"ts":1780779595,"hash":"four"}})
        idx=Indexer(Settings(history_from=iso,gonka_start=5,eth_start=5),self.db)
        await idx.net.close()
        self.assertEqual(idx.history_verified,{"gonka","ethereum"})
        idx=Indexer(Settings(history_from=iso,gonka_start=6,eth_start=6),self.db)
        await idx.net.close()
        self.assertEqual(idx.history_verified,set())
        idx=Indexer(Settings(history_from="2026-06-08T00:00:00+03:00",gonka_start=5,eth_start=5),self.db)
        await idx.net.close()
        self.assertEqual(idx.history_verified,set())

    async def test_failed_native_batch_does_not_advance_and_retry_has_full_coverage(self):
        self.idx.native_ready.set()
        self.db.put("gonka_back",24)
        self.db.put("gonka_target",1)
        async def pair_data(lo,hi):
            payload=[{"id":f"{m}:{h}","method":m,"params":{"height":str(h)}}
                     for h in range(lo,hi+1) for m in ("block","block_results")]
            rows=native_reply(payload)
            pairs=list(zip(rows[::2],rows[1::2]))
            return pairs
        good=await pair_data(13,24)
        good[-1][1]["result"]["height"]="25"
        self.idx.net.native_blocks=AsyncMock(return_value=good)
        with self.assertRaises(ValueError): await self.idx.native_history()
        self.assertEqual(self.db.get("gonka_back"),24)
        self.assertEqual(self.db.coverage("gonka")["blocks"],0)
        self.idx.net.native_blocks=pair_data
        await self.idx.native_history()
        self.assertEqual(self.db.get("gonka_back"),12)
        self.assertEqual(self.db.coverage("gonka")["blocks"],12)


if __name__=="__main__": unittest.main()
