from commands.models import *
from commands.config import get_config_item
from commands.cli_wrappers import get_node_client
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from typing import Tuple
from web3 import Web3
import time
import logging
import json
import asyncio

class ChiaFollower:
  chain: str
  chain_id: bytes
  node: FullNodeRpcClient
  db: any
  
  def __init__(self, chain: str):
        self.chain = chain
        self.chain_id = chain.encode()
        self.db = setup_database()
        self.node = None

  async def setupNode(self):
      if self.node is None:
          self.node = await get_node_client(self.chain_name)

  async def signer(self):
      pass

  async def run(self):
      await self.setupNode()

      # todo: block follower + message follower task
      # self.block_follower_task = asyncio.create_task(self.blockFollower())
      # todo: portal follower task
      self.signer_task = asyncio.create_task(self.signer())

      await self.signer_task     
