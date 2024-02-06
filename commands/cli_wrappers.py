import functools
from chia.util.config import load_config
from commands.config import get_config_item
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from chia.util.ints import uint16
from pathlib import Path
import sys
import asyncio
from functools import update_wrapper

def async_func(f):
    f = asyncio.coroutine(f)
    def wrapper(*args, **kwargs):
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(f(*args, **kwargs))
    return update_wrapper(wrapper, f)

async def get_node_client() -> FullNodeRpcClient:
    try:  
        root_path = Path(get_config_item(["chia", "chia_root"]))
        print("yak", root_path)
        config = load_config(root_path, "config.yaml")
        self_hostname = config["self_hostname"]
        rpc_port = config["full_node"]["rpc_port"]
        node_client: FullNodeRpcClient = await FullNodeRpcClient.create(
            self_hostname, uint16(rpc_port), root_path, config
        )
        await node_client.healthz()
        return node_client
    except:
        print("Failed to connect to the full node - check chia_root.")
        sys.exit(1)

def with_node(f):
    @functools.wraps(f)
    async def wrapper(*args, **kwargs):
        node_client = await get_node_client()

        res = await f(*args, **kwargs, node=node_client)

        node_client.close()
        await node_client.await_closed()

        return res
    
    return wrapper
