from commands.models import *
from commands.config import get_config_item
from commands.cli_wrappers import get_node_client
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from typing import Tuple
from web3 import Web3
import logging
import json
import asyncio

class ChiaFollower:
    chain: str
    chain_id: bytes
    

    def __init__(self, chain: str):
            self.chain = chain
            self.chain_id = chain.encode()


    def getDb(self):
        return setup_database()


    async def getNode(self):
        return await get_node_client(self.chain)


    async def signMessage(self, message: Message):
            if message.sig != b'':
                return
            
            logging.info(f"{self.chain}: Signing message {message.source_chain.decode()}-0x{message.nonce.hex()}")

    async def signer(self):
        db = self.getDb()

        while True:
            messages = []
            try:
                messages = db.query(Message).filter(
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

            await asyncio.sleep(5)


    async def portalFollower(self):
        pass


    def run(self):
        self.loop = asyncio.get_event_loop()

        self.loop.create_task(self.signer())
        # todo: block follower + message follower task
        # self.block_follower_task = asyncio.create_task(self.blockFollower())
        #   self.portal_follower_task = asyncio.create_task(self.portalFollower())

        if not self.loop.is_running():
            self.loop.run_forever()
