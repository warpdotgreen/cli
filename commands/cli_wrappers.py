import functools
from chia.util.config import load_config
from commands.config import get_config_item
from commands.http_full_node_rpc_client import HTTPFullNodeRpcClient
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from chia.util.ints import uint16
from pathlib import Path
import sys
import asyncio
from functools import wraps
import logging

def async_func(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        loop = asyncio.get_event_loop()
        if loop.is_running():
            return asyncio.create_task(f(*args, **kwargs))
        else:
            try:
                result = loop.run_until_complete(f(*args, **kwargs))
                return result
            finally:
                loop.close()

    return wrapper

async def get_node_client(chain_name: str = "xch") -> FullNodeRpcClient:
    try:
        try:
            chia_url = get_config_item([chain_name, "chia_url"])
            node_client = HTTPFullNodeRpcClient(chia_url)
            await node_client.healthz()
            return node_client
        except Exception as e:
            # logging.error("Failed to get node using specified url", exc_info=True)
            pass  
        root_path = Path(get_config_item([chain_name, "chia_root"]))
        config = load_config(root_path, "config.yaml")
        self_hostname = config["self_hostname"]
        rpc_port = config["full_node"]["rpc_port"]
        node_client: FullNodeRpcClient = await FullNodeRpcClient.create(
            self_hostname, uint16(rpc_port), root_path, config
        )
        await node_client.healthz()
        return node_client
    except Exception as e:
        logging.error("Failed to get node; retrying in 5 s. Error:")
        logging.error(e)
        await asyncio.sleep(5)
        return get_node_client(chain_name)

def with_node(f):
    @functools.wraps(f)
    async def wrapper(*args, **kwargs):
        node_client = await get_node_client()
        if node_client is None:
            print("Failed to connect to the full node - check chia_root.")
            sys.exit(1)

        res = await f(*args, **kwargs, node=node_client)

        node_client.close()
        await node_client.await_closed()

        return res
    
    return wrapper
