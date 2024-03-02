from commands.models import *
from commands.config import get_config_item
from commands.cli_wrappers import get_node_client
from chia.rpc.full_node_rpc_client import FullNodeRpcClient
from chia.util.condition_tools import conditions_dict_for_solution
from chia.types.blockchain_format.program import INFINITE_COST
from chia.types.condition_opcodes import ConditionOpcode
from chia.types.blockchain_format.coin import Coin
from chia.types.blockchain_format.program import Program
from chia.consensus.block_record import BlockRecord
from chia.types.coin_record import CoinRecord
from commands.followers.sig import encode_signature, decode_signature
from typing import Tuple
import logging
import asyncio
from sqlalchemy import and_
from blspy import AugSchemeMPL, PrivateKey

SIG_USED_VALUE = b"used"

class ChiaFollower:
    chain: str
    chain_id: bytes
    private_key: PrivateKey
    sign_min_height: int
    unspent_portal_id: bytes
    unspent_portal_id_lock: asyncio.Lock
    bridging_puzzle_hash: bytes
    per_message_fee: bytes

    def __init__(self, chain: str):
        self.chain = chain
        self.chain_id = chain.encode()
        self.private_key = PrivateKey.from_bytes(bytes.fromhex(get_config_item([chain, "my_hot_private_key"])))
        self.sign_min_height = int(get_config_item([chain, "sign_min_height"]))
        self.unspent_portal_id = None
        self.unspent_portal_id_lock = asyncio.Lock()
        self.bridging_puzzle_hash = bytes.fromhex(get_config_item([chain, "bridging_ph"]))
        self.per_message_fee = int(get_config_item([chain, "per_message_fee"]))


    async def getUnspentPortalId(self) -> bytes:
        coin_id = None
        while coin_id is None:
            async with self.unspent_portal_id_lock:
                coin_id = self.unspent_portal_id
            if coin_id is None:
                await asyncio.sleep(0.1)
        return coin_id
    

    async def setUnspentPortalId(self, coin_id: bytes):
        async with self.unspent_portal_id_lock:
            self.unspent_portal_id = coin_id


    def getDb(self):
        return setup_database()


    async def getNode(self):
        return await get_node_client(self.chain)


    async def signMessage(self, db, message: Message):
        logging.info(f"{self.chain}: Signing message {message.source_chain.decode()}-0x{message.nonce.hex()}")

        assert message.destination_chain == self.chain_id
        source = message.source
        while source.startswith(b'\x00'):
            source = source[1:]
        # source_chain nonce source destination message
        msg_bytes: bytes = Program(Program.to([
            message.source_chain,
            message.nonce,
            source,
            message.destination,
            split_message_contents(message.contents)
        ])).get_tree_hash()
        
        portal_id = await self.getUnspentPortalId()
        portal_state = db.query(ChiaPortalState).filter(
            ChiaPortalState.coin_id == portal_id
        ).first()
        if portal_state is None:
            logging.error(f"Portal coin {self.chain}-0x{portal_id.hex()}: not found in db. Not signing message.")
            return
        
        for used_chain_and_nonce in Program.from_bytes(portal_state.used_chains_and_nonces).as_iter():
            if used_chain_and_nonce.first().as_atom() == message.source_chain and used_chain_and_nonce.rest().as_atom() == message.nonce:
                logging.error(f"Chain {self.chain}-0x{message.nonce.hex()}: nonce already used. Not signing message.")
                message.sig = SIG_USED_VALUE
                db.commit()
                return
        
        msg_bytes = msg_bytes + portal_id + bytes.fromhex(get_config_item([self.chain, "agg_sig_data"]))

        sig = AugSchemeMPL.sign(self.private_key, msg_bytes)
        logging.info(f"{self.chain} Signer: {message.source_chain.decode()}-{message.nonce.hex()}: Raw signature: {bytes(sig).hex()}")

        message.sig = encode_signature(
            message.source_chain,
            message.destination_chain,
            message.nonce,
            portal_id,
            bytes(sig)
        ).encode()
        db.commit()
        logging.info(f"{self.chain} Signer: {message.source_chain.decode()}-{message.nonce.hex()}: Signature: {message.sig.decode()}")

        # todo: replace with nostr
        open("messages.txt", "a").write(message.sig.decode() + "\n")


    async def signer(self):
        db = self.getDb()

        while True:
            messages = []
            try:
                messages = db.query(Message).filter(and_(
                    Message.destination_chain == self.chain_id,
                    Message.sig == b'',
                    Message.sig != SIG_USED_VALUE,
                    Message.has_enough_confirmations_for_signing.is_(True)
                )).all()
            except Exception as e:
                logging.error(f"Error querying messages: {e}")
                logging.error(e)
                pass

            for message in messages:
                try:
                    await self.signMessage(db, message)
                    db.commit()
                except Exception as e:
                    logging.error(f"Error signing message {message.nonce.hex()}: {e}")
                    logging.error(e)

            await asyncio.sleep(5)

    def revertBlock(self, db, height: int):
      block = db.query(Block).filter(and_(Block.height == height, Block.chain_id == self.chain_id)).first()
      block_hash = block.hash
      db.query(Message).filter(
         and_(
            Message.source_chain == self.chain_id,
            Message.block_number >= height
          )
      ).delete()
      portal_states: List[ChiaPortalState] = db.query(ChiaPortalState).filter(and_(
         ChiaPortalState.chain_id == self.chain_id,
         ChiaPortalState.confirmed_block_height >= height
      )).all()
      for portal_state in portal_states:
        portal_state.confirmed_block_height = None
      db.delete(block)
      logging.info(f"Block #{self.chain_id.decode()}-{height} reverted.")


    def addBlock(
        self,
        db,
        node: FullNodeRpcClient,
        height: int,
        block_hash: bytes,
        prev_hash: bytes,
    ):
        db.add(Block(
            chain_id=self.chain_id,
            height=height,
            hash=block_hash,
            prev_hash=prev_hash
        ))
        logging.info(f"Block #{self.chain_id.decode()}-{height} added to db.")

        messages: List[Message] = db.query(Message).filter(and_(
            Message.source_chain == self.chain_id,
            Message.has_enough_confirmations_for_signing.is_(False),
            Message.block_number < height - self.sign_min_height
        )).all()
        for message in messages:
            msg_block: Block = db.query(Block).filter(and_(
                Block.height == message.block_number,
                Block.chain_id == self.chain_id
            )).first()
            if msg_block is None or msg_block.hash != message.block_hash:
                logging.error(f"Message {self.chain_id.decode()}-{message.nonce.hex()} - message block hash different from block hash in db. Not signing.")
                continue
            
            logging.info(f"Message {self.chain_id.decode()}-{message.nonce.hex()} has enough confirmations; marked for signing.")
            message.has_enough_confirmations_for_signing = True


    async def syncBlock(self, db, node: FullNodeRpcClient, block_height: int, prev_hash: bytes) -> Tuple[int, bytes]:
        block_record: BlockRecord = await node.get_block_record_by_height(block_height)
        if block_record is None:
            await asyncio.sleep(10)
            return block_height, prev_hash

        # check prev hash and if it matches the info we have from db
        # if not, revert the block
        block_hash = block_record.header_hash
        logging.info(f"Processing block #{self.chain_id.decode()}-{block_height} with hash {block_hash.hex()}...")
        if prev_hash is None:
            prev_hash = db.query(Block.hash).filter(and_(
                Block.height == block_height - 1, Block.chain_id == self.chain_id
            )).first()
            if prev_hash is None:
                if block_height == get_config_item([self.chain, 'min_height']):
                    logging.info(f"Block #{self.chain_id.decode()}-{block_height} is the first block in db.")
                    self.addBlock(db, node, block_height, block_hash, block_record.prev_hash)
                    return block_height + 1, block_hash
                logging.info(f"Block #{self.chain_id.decode()}-{block_height-1} not in db - soft reverting.")
                return block_height - 1, None
            prev_hash = prev_hash[0]
        
        if prev_hash != block_record.prev_hash:
            self.revertBlock(db, block_height - 1)
            return block_height - 1, None
        
        # check if a block with the same height is already in db
        current_block = db.query(Block).filter(and_(
            Block.height == block_height, Block.chain_id == self.chain_id
        )).first()
        if current_block is not None:
            if current_block.hash != block_hash or current_block.prev_hash != block_record.prev_hash:
                logging.info(f"Another block #{self.chain_id.decode()}-{block_height} in db - reverting.")
                self.revertBlock(db, block_height)
                return block_height, None
            
            logging.info(f"Block #{self.chain_id.decode()}-{block_height} already in db.")
            return block_height + 1, None

        # yep, let's add this block to the db!
        self.addBlock(db, node, block_height, block_hash, block_record.prev_hash)
        return block_height + 1, block_hash
    
    async def blockFollower(self):
        db = self.getDb()
        node = await self.getNode()

        latest_block_in_db = db.query(Block).filter(
            Block.chain_id == self.chain_id
        ).order_by(Block.height.desc()).first()
        latest_synced_block_height: int = latest_block_in_db.height if latest_block_in_db is not None else get_config_item([self.chain, 'min_height']) - 1
        logging.info(f"Sync peak: {self.chain_id.decode()}-{latest_synced_block_height}")

        next_block_height = latest_synced_block_height + 1
        prev_hash = None
        while True:
            next_block_height, prev_hash = await self.syncBlock(db, node, next_block_height, prev_hash)
            db.commit()

        node.close()
        await node.await_closed()


    async def syncPortal(
        self,
        db,
        node: FullNodeRpcClient,
        last_synced_portal: ChiaPortalState
    ) -> ChiaPortalState:
        coin_record = await node.get_coin_record_by_name(last_synced_portal.coin_id)
        if coin_record.spent_block_index == 0:
            parent_coin_record = await node.get_coin_record_by_name(last_synced_portal.parent_id)
            if parent_coin_record.spent_block_index == 0:
                logging.info(f"Portal coin {self.chain}-0x{last_synced_portal.coin_id.hex()}: parent is unspent; reverting.")
                parent_state = db.query(ChiaPortalState).filter(
                    ChiaPortalState.coin_id == last_synced_portal.parent_id
                ).first()
                last_synced_portal.confirmed_block_height = None
                return parent_state
            
            # else, unspent - just wait patiently
            await self.setUnspentPortalId(last_synced_portal.coin_id)
            await asyncio.sleep(5)
            return last_synced_portal

        # spent!
        spend = await node.get_puzzle_and_solution(last_synced_portal.coin_id, coin_record.spent_block_index)
        conds = conditions_dict_for_solution(spend.puzzle_reveal, spend.solution, INFINITE_COST)
        create_coins = conds[ConditionOpcode.CREATE_COIN]
        new_ph = None
        for cond in create_coins:
            if cond.vars[1] == b'\x01':
                new_ph = cond.vars[0]
                break
        if new_ph is None:
            logging.error(f"Portal coin {self.chain}-0x{last_synced_portal.coin_id.hex()}: no singleton found in spend; reverting.")
            parent_state = db.query(ChiaPortalState).filter(
                ChiaPortalState.coin_id == last_synced_portal.parent_id
            ).first()
            last_synced_portal.confirmed_block_height = None
            return parent_state

        inner_solution: Program = Program.from_bytes(bytes(spend.solution)).at("rrf")
        update_package = inner_solution.at("f")
        assert bytes(update_package) == bytes(Program.to(0))

        prev_used_chains_and_nonces = Program.from_bytes(last_synced_portal.used_chains_and_nonces).as_python()
        if len(prev_used_chains_and_nonces) == 0:
            prev_used_chains_and_nonces = []
        
        chains_and_nonces = inner_solution.at("rf").as_iter()
        for cn in chains_and_nonces:
            source_chain = cn.first().as_atom()
            nonce = cn.rest().as_atom()
            prev_used_chains_and_nonces.append(
                (source_chain, nonce)
            )

            msg = db.query(Message).filter(and_(
                Message.source_chain == source_chain,
                Message.nonce == nonce
            )).first()
            msg.sig = SIG_USED_VALUE

        chains_and_nonces = bytes(Program.to(prev_used_chains_and_nonces))
       
        new_singleton = Coin(
            last_synced_portal.coin_id,
            new_ph,
            1
        )

        db.query(ChiaPortalState).filter(
            ChiaPortalState.parent_id == new_singleton.parent_coin_info
        ).delete()
        new_synced_portal = ChiaPortalState(
            chain_id=self.chain_id,
            coin_id=new_singleton.name(),
            parent_id=new_singleton.parent_coin_info,
            used_chains_and_nonces=chains_and_nonces,
            confirmed_block_height=coin_record.spent_block_index,
        )
        db.add(new_synced_portal)
        db.commit()

        logging.info(f"New portal coin: {self.chain}-0x{new_synced_portal.coin_id.hex()}")

        await self.setUnspentPortalId(new_synced_portal.coin_id)

        messages = db.query(Message).filter(and_(
            Message.destination_chain == self.chain_id,
            Message.sig != SIG_USED_VALUE,
            Message.has_enough_confirmations_for_signing.is_(True)
        )).all()
        for message in messages:
            _, __, ___, coin_id, ____ = decode_signature(message.sig.decode())
            if coin_id != new_synced_portal.coin_id:
                await self.signMessage(db, message)

        return new_synced_portal
    


    async def portalFollower(self):
        db = self.getDb()
        node = await self.getNode()

        portal_launcher_id: bytes = bytes.fromhex(get_config_item([self.chain, "portal_launcher_id"]))
        last_synced_portal = db.query(ChiaPortalState).filter(and_(
            ChiaPortalState.chain_id == self.chain_id,
            ChiaPortalState.confirmed_block_height != None
        )).order_by(ChiaPortalState.confirmed_block_height.desc()).first()

        if last_synced_portal is None:
            logging.info(f"{self.chain}: No last synced portal found, using launcher...")
            launcher_coin_record = await node.get_coin_record_by_name(portal_launcher_id)
            assert launcher_coin_record.spent_block_index > 0

            launcher_spend = await node.get_puzzle_and_solution(portal_launcher_id, launcher_coin_record.spent_block_index)
            conds = conditions_dict_for_solution(launcher_spend.puzzle_reveal, launcher_spend.solution, INFINITE_COST)
            create_coins = conds[ConditionOpcode.CREATE_COIN]
            assert len(create_coins) == 1 and create_coins[0].vars[1] == b'\x01'

            singleton_full_puzzle_hash = create_coins[0].vars[0]
            first_singleton = Coin(
                portal_launcher_id,
                singleton_full_puzzle_hash,
                1
            )

            last_synced_portal = ChiaPortalState(
                chain_id=self.chain_id,
                coin_id=first_singleton.name(),
                parent_id=portal_launcher_id,
                used_chains_and_nonces=bytes(Program.to([])),
                confirmed_block_height=launcher_coin_record.spent_block_index,
            )
            db.add(last_synced_portal)
            db.commit()

        logging.info(f"Latest portal coin: {self.chain}-0x{last_synced_portal.coin_id.hex()}")

        while True:
            last_synced_portal = await self.syncPortal(db, node, last_synced_portal)
            db.commit()

        node.close()
        await node.await_closed()


    async def createMessageFromMemo(
            self,
            db,
            node: FullNodeRpcClient,
            nonce: bytes,
            source: bytes,
            created_height: int,
            memo: Program
    ):
        try:
            destination_chain = memo.first().as_atom()
            destination = memo.rest().first().as_atom()
            contents_unparsed = memo.rest().rest()
        except:
            logging.info(f"Coin {self.chain}-{nonce.hex()}: error when parsing memo; skipping")
            return
        
        contents = []
        for content in contents_unparsed.as_iter():
            c = content.as_atom()
            if len(c) < 32:
                c = b'\x00' * (32 - len(c)) + c
            if len(c) > 32:
                c = c[:32]
            contents.append(c)
        
        block_in_db: Block = db.query(Block).filter(and_(
            Block.chain_id == self.chain_id,
            Block.height == created_height
        )).first()
        while block_in_db is None:
            logging.info(f"Coin {self.chain}-{nonce.hex()}: block {created_height} not in db; retrying in 20s...")
            await asyncio.sleep(20)
            block_in_db = db.query(Block).filter(and_(
                Block.chain_id == self.chain_id,
                Block.height == created_height
            )).first()

        msg_in_db = db.query(Message).filter(and_(
            Message.nonce == nonce,
            Message.source_chain == self.chain_id
        )).first()
        if msg_in_db is not None:
            logging.info(f"Coin {self.chain}-{nonce.hex()}: message already in db; skipping")
            return
        
        msg = Message(
            nonce=nonce,
            source_chain=self.chain_id,
            source=source,
            destination_chain=destination_chain,
            destination=destination,
            contents=join_message_contents(contents),
            block_hash=block_in_db.hash,
            block_number=created_height,
            has_enough_confirmations_for_signing=False,
            sig=b''
        )
        db.add(msg)
        db.commit()
        logging.info(f"Message {self.chain}-{nonce.hex()} added to db.")


    async def processCoinRecord(self, db: any, node: FullNodeRpcClient, coin_record: CoinRecord):
        if coin_record.coin.amount < self.per_message_fee:
            logging.info(f"Coin {self.chain}-{coin_record.coin.name().hex()} - amount {coin_record.coin.amount} too low; not parsing message.")
            return
        
        parent_record = await node.get_coin_record_by_name(coin_record.coin.parent_coin_info)
        parent_spend = await node.get_puzzle_and_solution(
            coin_record.coin.parent_coin_info,
            parent_record.spent_block_index
        )

        try:
            _, output = parent_spend.puzzle_reveal.run_with_cost(INFINITE_COST, parent_spend.solution)
            for condition in output.as_iter():
                if condition.first().as_int() == 51: # CREATE_COIN
                    created_ph = condition.at('rf').as_atom()
                    created_amount = condition.at('rrf').as_int()

                    if created_ph == self.bridging_puzzle_hash and created_amount >= self.per_message_fee:
                        coin = Coin(parent_record.coin.name(), created_ph, created_amount)
                        try:
                            memo = condition.at('rrrf')
                        except:
                            logging.error(f"Coin {self.chain}-{coin.name().hex()} - error when parsing memo; skipping")
                            continue

                        try:
                            await self.createMessageFromMemo(
                                db,
                                node,
                                coin.name(),
                                parent_record.coin.puzzle_hash,
                                parent_record.spent_block_index,
                                memo
                            )
                        except Exception as e:
                            logging.error(f"Coin {self.chain}-{coin.name().hex()} - error when parsing memo to create message; skipping even though we shouldn't")
                            logging.error(e)
        except Exception as e:
            logging.error(f"Coin {self.chain}-{coin_record.coin.name().hex()} - error when parsing output; skipping")


    async def sentMessagesListener(self):
        db = self.getDb()
        node = await self.getNode()

        while True:
            last_message_height = db.query(Message.block_number).filter(
                Message.source_chain == self.chain_id
            ).order_by(Message.block_number.desc()).first()
            if last_message_height is None:
                last_message_height = [get_config_item([self.chain, 'min_height']) - 1]

            query_message_height = last_message_height[0] + 1
            records = await node.get_coin_records_by_puzzle_hash(
                self.bridging_puzzle_hash,
                include_spent_coins=True,
                start_height=query_message_height
            )

            for coin_record in records:
                await self.processCoinRecord(db, node, coin_record)

            await asyncio.sleep(10)

        node.close()
        await node.await_closed()


    def run(self, loop):
        self.loop = loop

        self.loop.create_task(self.signer())
        self.loop.create_task(self.blockFollower())
        self.loop.create_task(self.portalFollower())
        self.loop.create_task(self.sentMessagesListener())
