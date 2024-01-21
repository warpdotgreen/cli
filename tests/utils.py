"""
yak's note: the contents of this file were mainly taken from unreleased code from my next project
if something doesn't make sense, please update it, and also let me know so I can fix it in the original codebase :)
"""

from chia.full_node.mempool_check_conditions import get_name_puzzle_conditions
from chia.full_node.bundle_tools import simple_solution_generator
from chia.types.blockchain_format.program import INFINITE_COST
from chia.consensus.default_constants import DEFAULT_CONSTANTS
from chia.types.generator_types import BlockGenerator
from chia.consensus.cost_calculator import NPCResult
from chia.types.spend_bundle import SpendBundle
from chia.simulator.setup_nodes import setup_simulators_and_wallets_service
from chia.simulator.block_tools import test_constants
import dataclasses
import pytest_asyncio
from typing import List
from chia.server.start_service import Service
from chia.full_node.full_node import FullNode
from chia.simulator.full_node_simulator import FullNodeSimulator
from chia.wallet.wallet_node import WalletNode
from chia.rpc.rpc_server import RpcServer
from chia.wallet.wallet_node_api import WalletNodeAPI
from chia.simulator.block_tools import BlockTools
from chia.server.server import ChiaServer
from chia.simulator.simulator_full_node_rpc_api import SimulatorFullNodeRpcApi
from chia.rpc.rpc_server import start_rpc_server
from chia.util.ints import uint16
from chia.rpc.wallet_rpc_api import WalletRpcApi
from chia.types.peer_info import PeerInfo
from chia.types.spend_bundle import SpendBundle
from chia.simulator.simulator_full_node_rpc_client import SimulatorFullNodeRpcClient
from chia.rpc.wallet_rpc_client import WalletRpcClient
from chia.wallet.puzzles.p2_delegated_puzzle_or_hidden_puzzle import *


# taken from https://github.com/Chia-Network/chia-dev-tools/blob/main/cdv/cmds/chia_inspect.py
def get_spend_bundle_cost(spend_bundle: SpendBundle) -> int:
    program: BlockGenerator = simple_solution_generator(spend_bundle)
    npc_result: NPCResult = get_name_puzzle_conditions(
        program,
        INFINITE_COST,
        height=DEFAULT_CONSTANTS.SOFT_FORK2_HEIGHT,  # so that all opcodes are available
        mempool_mode=True,
        constants=DEFAULT_CONSTANTS,
    )
    return npc_result.cost

@pytest_asyncio.fixture(scope="function")
async def one_node_and_one_wallet_services():
    async with setup_simulators_and_wallets_service(
        1, 1, 
        consensus_constants=dataclasses.replace(
            test_constants,
            SOFT_FORK2_HEIGHT=0,
        )
    ) as (full_node_services, wallet_services, bt):
        yield full_node_services, wallet_services, bt

@pytest_asyncio.fixture(scope="function")
async def setup(one_node_and_one_wallet_services):
    async for _ in _setup(one_node_and_one_wallet_services):
        yield _

async def _setup(node_and_wallet_services):
    full_node_services: List[Service[FullNode, FullNodeSimulator]]
    wallet_services: List[Service[WalletNode, WalletNodeAPI]]
    block_tools: BlockTools

    full_node_services, wallet_services, block_tools = node_and_wallet_services
    assert len(full_node_services) == 1
    assert len(wallet_services) in [1, 2]

    full_node_service: Service[FullNode, FullNodeSimulator] = full_node_services[0]
    full_node_simulator: FullNodeSimulator = full_node_service._api
    full_node_server: ChiaServer = full_node_simulator.server
    
    config = block_tools.config
    daemon_port = config["daemon_port"]
    self_hostname = config["self_hostname"]

    def stop_node_cb() -> None:
        pass

    full_node_rpc_api = SimulatorFullNodeRpcApi(
        full_node_simulator.full_node)

    rpc_server_node = await start_rpc_server(
        full_node_rpc_api,
        self_hostname,
        daemon_port,
        uint16(0),
        stop_node_cb,
        block_tools.root_path,
        config,
        connect_to_daemon=False,
    )

    sim_full_node_rpc_client: SimulatorFullNodeRpcClient = await SimulatorFullNodeRpcClient.create(
        self_hostname, rpc_server_node.listen_port, block_tools.root_path, config
    )
    await sim_full_node_rpc_client.set_auto_farming(True)

    wallet_nodes: List[WalletNode] = []
    wallet_servers: List[ChiaServer] = []
    wallet_rpc_apis: List[WalletRpcApi] = []

    for wallet_node_service in wallet_services:
        wallet_node: WalletNode = wallet_node_service._node
        wallet_nodes.append(wallet_node)

        wallet_server: ChiaServer = wallet_node.server
        wallet_servers.append(wallet_server)

        wallet_server.config["trusted_peers"] = {
            full_node_server.node_id.hex(): full_node_server.node_id.hex()
        }

        await wallet_server.start_client(PeerInfo("127.0.0.1", uint16(full_node_server._port)), None)

        await full_node_simulator.farm_blocks_to_wallet(2, wallet=wallet_node.wallet_state_manager.main_wallet)

        wallet_rpc_apis.append(WalletRpcApi(wallet_server))

    wallet_rpc_servers: List[RpcServer] = []
    wallet_rpc_clients: List[WalletRpcClient] = []
    for wallet_node in wallet_nodes:
        wallet_rpc_server = await start_rpc_server(
            WalletRpcApi(wallet_node),
            self_hostname,
            daemon_port,
            uint16(0),
            lambda x: None,  # type: ignore
            block_tools.root_path,
            config,
            connect_to_daemon=False,
        )
        wallet_rpc_servers.append(wallet_rpc_server)

        wallet_rpc_client: WalletRpcClient = await WalletRpcClient.create(
            self_hostname, wallet_rpc_server.listen_port, block_tools.root_path, config
        )
        wallet_rpc_clients.append(wallet_rpc_client)

    for wallet_node in wallet_nodes:
        await full_node_simulator.wait_for_wallet_synced(wallet_node)

    yield sim_full_node_rpc_client, wallet_rpc_clients

    for wallet_rpc_client in wallet_rpc_clients:
        wallet_rpc_client.close()
    sim_full_node_rpc_client.close()

    for wallet_rpc_server in wallet_rpc_servers:
        wallet_rpc_server.close()
    rpc_server_node.close()
        
    for wallet_rpc_client in wallet_rpc_clients:
        await wallet_rpc_client.await_closed()
    await sim_full_node_rpc_client.await_closed()

    for wallet_rpc_server in wallet_rpc_servers:
        await wallet_rpc_server.await_closed()
    await rpc_server_node.await_closed()
