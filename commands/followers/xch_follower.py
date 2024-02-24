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
          self.node = await get_node_client(self.chain)

  async def signMessage(self, message: Message):
        if message.sig != b'':
            return
        
        logging.info(f"{self.chain}: Signing message {message.source_chain.decode()}-0x{message.nonce.hex()}")

  async def signer(self):
      while True:
        messages = []
        try:
            messages = self.db.query(Message).filter(
                Message.destination_chain == self.chain_id and Message.sig == b''
            ).all()
        except Exception as e:
            logging.error(f"Error querying messages: {e}")
            logging.error(e)
            pass

        for message in messages:
            try:
                await self.signMessage(message)
            except Exception as e:
                logging.error(f"Error signing message {message.nonce.hex()}: {e}")
                logging.error(e)

        time.sleep(5)

  async def portalFollower(self):
      pass

  async def run(self):
      await self.setupNode()

      # todo: block follower + message follower task
      # self.block_follower_task = asyncio.create_task(self.blockFollower())
      self.portal_follower_task = asyncio.create_task(self.portalFollower())
      # self.signer_task = asyncio.create_task(self.signer())

      # await self.signer_task     
      await self.portal_follower_task
