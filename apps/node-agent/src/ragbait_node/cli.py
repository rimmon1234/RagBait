"""Command-line interface for the RagBait Ollama Node Agent."""

import argparse
import asyncio
import logging
import platform
import sys

import httpx

from ragbait_node import crypto, ollama
from ragbait_node.client import NodeClient
from ragbait_node.config import NodeConfig

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ragbait-node")


def main() -> None:
    """Entry point for the ragbait-node command-line interface."""
    parser = argparse.ArgumentParser(
        prog="ragbait-node",
        description="RagBait Ollama Node Agent - Secure Shared Community Local Inference",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. pair
    pair_parser = subparsers.add_parser(
        "pair", help="Pair this device using a RagBait pairing code"
    )
    pair_parser.add_argument("--code", required=True, help="One-time pairing code from RagBait UI")
    pair_parser.add_argument(
        "--server",
        default="http://127.0.0.1:8000",
        help="RagBait server HTTP URL (default: http://127.0.0.1:8000)",
    )
    pair_parser.add_argument(
        "--name",
        default="",
        help="Optional friendly name for this node (default: hostname)",
    )
    pair_parser.add_argument(
        "--models",
        default="",
        help="Optional comma-separated list of local models to allow (e.g. llama3:8b,mistral:7b)",
    )

    # 2. run
    run_parser = subparsers.add_parser("run", help="Start the node agent in the foreground")
    run_parser.add_argument(
        "--models",
        default="",
        help="Optional comma-separated list of local models to allow (overrides saved config)",
    )

    # 3. status
    _ = subparsers.add_parser("status", help="Show current node pairing and Ollama status")

    # 4. unpair
    _ = subparsers.add_parser("unpair", help="Revoke and delete local node pairing")

    args = parser.parse_args()

    if args.command == "pair":
        asyncio.run(handle_pair(args.code, args.server, args.name, args.models))
    elif args.command == "run":
        asyncio.run(handle_run(args.models))
    elif args.command == "status":
        asyncio.run(handle_status())
    elif args.command == "unpair":
        handle_unpair()


async def handle_pair(
    code: str, server_url: str, custom_name: str, models_str: str = ""
) -> None:
    """Execute device pairing flow."""
    server_url = server_url.rstrip("/")
    config = NodeConfig.load()
    config.server_url = server_url
    ws_protocol = "wss" if server_url.startswith("https") else "ws"
    config.ws_url = f"{ws_protocol}://{server_url.split('://')[1]}/api/v1/community/ws"
    if models_str:
        config.allowed_models = [m.strip() for m in models_str.split(",") if m.strip()]

    print(f"[*] Initializing local cryptographic identity at {config.key_file}...")
    private_key = crypto.get_or_create_keypair(config.key_file)
    public_key_hex = crypto.get_public_key_hex(private_key)

    display_name = custom_name.strip() or f"{platform.node()}-{platform.system()}"

    # Discover Ollama metadata
    alive, version = await ollama.check_ollama_alive()
    ollama_ver = version if alive else None

    pair_payload = {
        "pairing_code": code.strip(),
        "public_key": public_key_hex,
        "display_name": display_name[:64],
        "agent_version": "0.1.0",
        "os": platform.system(),
        "architecture": platform.machine(),
        "ollama_version": ollama_ver,
    }

    print(f"[*] Contacting RagBait server at {server_url}/api/v1/community/nodes/pair...")
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            res = await client.post(
                f"{server_url}/api/v1/community/nodes/pair",
                json=pair_payload,
            )
        except Exception as e:
            print(f"[!] Error contacting server: {e}")
            sys.exit(1)

    if res.status_code != 201:
        print(f"[!] Pairing failed (HTTP {res.status_code}): {res.text}")
        sys.exit(1)

    data = res.json()
    config.node_id = data["node_id"]
    config.display_name = data["display_name"]
    config.server_public_key = data["server_public_key"]
    config.server_key_id = data["server_key_id"]
    config.save()

    print("\n" + "=" * 60)
    print("✓ NODE SUCCESSFULLY PAIRED WITH RAGBAIT!")
    print(f"  Node ID:           {config.node_id}")
    print(f"  Display Name:      {config.display_name}")
    print(f"  Public Key (Hex):  {public_key_hex[:16]}...{public_key_hex[-8:]}")
    print(f"  Server Key ID:     {config.server_key_id}")
    print("=" * 60)
    print("\nNext steps:")
    print("1. Open the RagBait web interface (/community-nodes).")
    print("2. Enable community sharing and select which models to share.")
    print("3. Start the agent by running:\n   ragbait-node run\n")


async def handle_run(models_str: str = "") -> None:
    """Start the node agent."""
    config = NodeConfig.load()
    if not config.is_paired():
        print("[!] This device is not paired with a RagBait account yet.")
        print("    Please run: ragbait-node pair --code <PAIRING-CODE>")
        sys.exit(1)

    if models_str:
        config.allowed_models = [m.strip() for m in models_str.split(",") if m.strip()]

    client = NodeClient(config)
    try:
        await client.start()
    except KeyboardInterrupt:
        client.stop()


async def handle_status() -> None:
    """Display status of local node configuration and Ollama connection."""
    config = NodeConfig.load()
    print("=" * 60)
    print("RagBait Ollama Node Agent Status")
    print("=" * 60)
    print(f"Config Directory:   {config.config_dir}")
    print(f"Paired:             {config.is_paired()}")

    if config.is_paired():
        print(f"Node ID:            {config.node_id}")
        print(f"Display Name:       {config.display_name}")
        print(f"Server URL:         {config.server_url}")
        print(f"WebSocket URL:      {config.ws_url}")
        print(f"Server Key ID:      {config.server_key_id}")
        if config.allowed_models:
            print(f"Allowed Models:     {', '.join(config.allowed_models)}")
        else:
            print("Allowed Models:     (All installed models allowed)")

    alive, ver = await ollama.check_ollama_alive()
    print(f"Local Ollama:       {'Online (v' + ver + ')' if alive else 'Offline / Unreachable'}")

    if alive:
        models = await ollama.list_ollama_models()
        print(f"Installed Models ({len(models)}):")
        for m in models:
            name = m.get("name", "unknown")
            size = m.get("size", 0)
            size_mb = f"{size / (1000 * 1000):.0f} MB" if size else ""
            print(f"  • {name} ({size_mb})")
    print("=" * 60)


def handle_unpair() -> None:
    """Revoke local configuration files."""
    config = NodeConfig.load()
    if not config.config_dir.exists():
        print("No local configuration found.")
        return

    confirm = input(
        "Are you sure you want to delete local credentials and unpair this node? [y/N]: "
    )
    if confirm.lower().strip() != "y":
        print("Aborted.")
        return

    if config.key_file.exists():
        config.key_file.unlink()
    if config.config_file.exists():
        config.config_file.unlink()
    if config.state_db.exists():
        config.state_db.unlink()

    print("✓ Local pairing credentials and state removed.")


if __name__ == "__main__":
    main()
