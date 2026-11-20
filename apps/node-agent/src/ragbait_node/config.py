"""Local configuration and state directory management for RagBait Node Agent."""

import json
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_DIR = Path.home() / ".ragbait-node"
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.json"
DEFAULT_KEY_FILE = DEFAULT_CONFIG_DIR / "node_key.pem"
DEFAULT_STATE_DB = DEFAULT_CONFIG_DIR / "state.db"
DEFAULT_SERVER_URL = "http://127.0.0.1:8000"
DEFAULT_WS_URL = "ws://127.0.0.1:8000/api/v1/community/ws"


class NodeConfig:
    """Agent local configuration loaded from ~/.ragbait-node/config.json."""

    def __init__(
        self,
        node_id: str = "",
        display_name: str = "",
        server_url: str = DEFAULT_SERVER_URL,
        ws_url: str = DEFAULT_WS_URL,
        server_public_key: str = "",
        server_key_id: str = "k1",
        config_dir: Path = DEFAULT_CONFIG_DIR,
    ):
        self.node_id = node_id
        self.display_name = display_name
        self.server_url = server_url
        self.ws_url = ws_url
        self.server_public_key = server_public_key
        self.server_key_id = server_key_id
        self.config_dir = config_dir
        self.config_file = config_dir / "config.json"
        self.key_file = config_dir / "node_key.pem"
        self.state_db = config_dir / "state.db"

    @classmethod
    def load(cls, config_dir: Path = DEFAULT_CONFIG_DIR) -> "NodeConfig":
        """Load configuration from disk or return unconfigured instance."""
        config_file = config_dir / "config.json"
        if not config_file.exists():
            return cls(config_dir=config_dir)

        try:
            with open(config_file, encoding="utf-8") as f:
                data: dict[str, Any] = json.load(f)
            return cls(
                node_id=data.get("node_id", ""),
                display_name=data.get("display_name", ""),
                server_url=data.get("server_url", DEFAULT_SERVER_URL),
                ws_url=data.get("ws_url", DEFAULT_WS_URL),
                server_public_key=data.get("server_public_key", ""),
                server_key_id=data.get("server_key_id", "k1"),
                config_dir=config_dir,
            )
        except Exception:
            return cls(config_dir=config_dir)

    def save(self) -> None:
        """Atomically persist configuration to disk with restricted permissions."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "node_id": self.node_id,
            "display_name": self.display_name,
            "server_url": self.server_url,
            "ws_url": self.ws_url,
            "server_public_key": self.server_public_key,
            "server_key_id": self.server_key_id,
        }
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def is_paired(self) -> bool:
        """Return True if node has been paired with a valid node ID and key file."""
        return bool(self.node_id and self.key_file.exists() and self.server_public_key)
