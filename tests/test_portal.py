from chia.rpc.wallet_rpc_client import WalletRpcClient
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
import pytest
import os
import sys

from tests.utils import *
from drivers.portal import *

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class TestPortal:
    @pytest.mark.asyncio
    async def test_healthz(self, setup):
        full_node_client: FullNodeRpcClient
        wallet_clients: List[WalletRpcClient]
        full_node_client, wallet_clients = setup

        full_node_resp = await full_node_client.healthz()
        assert full_node_resp['success']

        wallet_client: WalletRpcClient = wallet_clients[0]
        wallet_resp = await wallet_client.healthz()
        assert wallet_resp['success']
