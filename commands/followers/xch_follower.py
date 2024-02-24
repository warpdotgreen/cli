from commands.models import *
from commands.config import get_config_item
from commands.cli_wrappers import get_node_client
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from typing import Tuple
from web3 import Web3
import logging
import json
import asyncio
from sqlalchemy import and_
from blspy import AugSchemeMPL, PrivateKey

class ChiaFollower:
    chain: str
    chain_id: bytes
    private_key: PrivateKey

    def __init__(self, chain: str):
        self.chain = chain
        self.chain_id = chain.encode()
        self.private_key = PrivateKey.from_bytes(bytes.fromhex(get_config_item([chain, "my_hot_private_key"])))

    def getDb(self):
        return setup_database()


    async def getNode(self):
        return await get_node_client(self.chain)


    async def signMessage(self, message: Message):
            if int(message.nonce.hex(), 16) <= 6: #todo: debug
                return
            logging.info(f"{self.chain}: Signing message {message.source_chain.decode()}-0x{message.nonce.hex()}")


    async def signer(self):
        db = self.getDb()

        while True:
            messages = []
            try:
                messages = db.query(Message).filter(and_(
                    Message.destination_chain == self.chain_id,
                    Message.sig == b'',
                    Message.has_enough_confirmations_for_signing.is_(True)
                )).all()
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
        db = self.getDb()
        node = await self.getNode()

        latest_synced_portal = None


    def run(self, loop):
        self.loop = loop

        # self.loop.create_task(self.signer())
        # todo: block follower + message follower task
        self.loop.create_task(self.portalFollower())
