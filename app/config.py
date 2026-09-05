"""Public, read-only data sources. No signing or wallet credentials."""
import os
from dataclasses import dataclass, field
from pathlib import Path

TOKEN = "0x972a7a92d92796a98801a8818bcf91f1648f2f68"
USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
FACTORY = "0x1f98431c8ad98523631ae4a59f267346ea31f984"
ZERO = "0x" + "0" * 40
ESCROW = "gonka1cjwjmyguyjaey70cgxxclxjh4wph3c8w0vvv63"
SEED_POOLS = [
    "0x203ee836d417cf944133bbdd2c62b4bc7388c55d",
    "0xdbc0edbced03c87a7f287f9949e01c012511f117",
]

def urls(name, defaults):
    return [x.strip().rstrip("/") for x in os.getenv(name, defaults).split(",") if x.strip()]

@dataclass
class Settings:
    mode: str = field(default_factory=lambda: os.getenv("APP_MODE", "full").strip().lower())
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", "data")))
    gonka: list = field(default_factory=lambda: urls("GONKA_URLS",
        "https://node1.gonka.ai:8443,https://node3.gonka.ai,https://node2.gonka.ai:8443"))
    gonka_rpc: list = field(default_factory=lambda: urls("GONKA_RPC_URLS", "https://rpc.gonka.gg"))
    gonka_archive: list = field(default_factory=lambda: urls("GONKA_ARCHIVE_RPC_URLS",
        "https://node1.gonka.ai:8443/chain-rpc,https://node2.gonka.ai:8443/chain-rpc,https://node3.gonka.ai/chain-rpc"))
    native_history_batch: int = field(default_factory=lambda: max(1, min(24, int(os.getenv("NATIVE_HISTORY_BATCH", "5")))))
    ethereum: list = field(default_factory=lambda: urls("ETH_RPC_URLS",
        "https://ethereum-rpc.publicnode.com,https://rpc.mevblocker.io"))
    ethereum_archive: list = field(default_factory=lambda: urls("ETH_ARCHIVE_RPC_URLS",
        "https://rpc.mevblocker.io"))
    eth_rpc_interval: float = field(default_factory=lambda: max(.5, float(os.getenv("ETH_RPC_INTERVAL", "1"))))
    eth_history_batch: int = field(default_factory=lambda: max(1, min(10000, int(os.getenv("ETH_HISTORY_BATCH", "1000")))))
    lookback_hours: int = field(default_factory=lambda: max(1, int(os.getenv("LOOKBACK_HOURS", "24"))))
    gonka_start: int = field(default_factory=lambda: int(os.getenv("GONKA_START_HEIGHT", "0")))
    eth_start: int = field(default_factory=lambda: int(os.getenv("ETH_START_HEIGHT", "0")))
    history_from: str = field(default_factory=lambda: os.getenv("HISTORY_FROM", "").strip())
    poll_seconds: int = field(default_factory=lambda: max(3, int(os.getenv("POLL_SECONDS", "6"))))
    native_batch: int = field(default_factory=lambda: max(1, min(16, int(os.getenv("NATIVE_BATCH", "6")))))
    indexer_enabled: bool = field(default_factory=lambda: os.getenv("INDEXER_ENABLED", "true").lower() == "true")
    seed_enabled: bool = field(default_factory=lambda: os.getenv("LOAD_SEED_ARCHIVE", "false").lower() == "true")

    def __post_init__(self):
        if self.mode not in ("full", "mints"):
            raise ValueError("APP_MODE must be full or mints")
        if self.history_from and self.mode == "full":
            from .history import timestamp
            timestamp(self.history_from)
            if self.gonka_start < 1 or self.eth_start < 1:
                raise ValueError("HISTORY_FROM requires exact GONKA_START_HEIGHT and ETH_START_HEIGHT; run python -m app.history --from ...")
