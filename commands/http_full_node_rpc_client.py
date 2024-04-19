# modified from the snippet obtained from the Goby team :heart:
import aiohttp
from chia.rpc.full_node_rpc_client import FullNodeRpcClient

class HTTPFullNodeRpcClient(FullNodeRpcClient):
    def __init__(self, base_url: str):
        super().__init__(None, None, None, None, None, None)
        self.session = aiohttp.ClientSession()
        self.closing_task = None
        self.base_url = base_url

    async def fetch(self, path, request_json):
        async with self.session.post(f"{self.base_url}/{path}", json=request_json) as response:
            response.raise_for_status()

            res_json = await response.json()
            if not res_json["success"]:
                raise ValueError(res_json)
            return res_json
