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
from commands.followers.sig import encode_signature, decode_signature, send_signature
from drivers.portal import BRIDGING_PUZZLE_HASH
from typing import Tuple
import logging
import asyncio
import sys
from sqlalchemy import and_
from chia_rs import AugSchemeMPL, PrivateKey

SIG_USED_VALUE = b"used"

class ChiaFollower:
    chain: str
    chain_id: bytes
    private_key: PrivateKey
    sign_min_height: int
    unspent_portal_id: bytes
    unspent_portal_id_lock: asyncio.Lock
    per_message_toll: bytes
    syncing: bool

    def __init__(self, chain: str):
        self.chain = chain
        self.chain_id = chain.encode()
        self.private_key = PrivateKey.from_bytes(bytes.fromhex(get_config_item([chain, "my_hot_private_key"])))
        self.sign_min_height = int(get_config_item([chain, "sign_min_height"]))
        self.unspent_portal_id = None
        self.unspent_portal_id_lock = asyncio.Lock()
        self.per_message_toll = int(get_config_item([chain, "per_message_toll"]))
        self.syncing = True


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


    # to save space, used_chains_and_nonces has a special format:
    # [([chain] [a] [nonce1] [nonce2]), ...]
    # the above would say that [chain] used all consecutive nonces from 1 to [a] (i.e., 1, 2, 3, ..., a) AND nonce1 AND nonce2
    def check_already_used_chain_and_nonce(
        self,
        used_data: bytes,
        source_chain: bytes,
        nonce: bytes
    ) -> bool:
        used_data: Program = Program.from_bytes(used_data)
        used_data_for_chain = None
        for used_chain_and_nonce in used_data.as_iter():
            if used_chain_and_nonce.first().as_atom() == source_chain:
                used_data_for_chain = used_chain_and_nonce.rest()
                break

        if used_data_for_chain is None:
            return False
        
        nonce: int = int(nonce.hex(), 16)
        if used_data_for_chain.first().as_int() >= nonce:
            return True
        
        for used_nonce in used_data_for_chain.rest().as_iter():
            if used_nonce.as_int() == nonce:
                return True
        return False


    # for format, see note of function above
    def add_chain_and_nonce(
        self,
        base_data: Program,
        source_chain: bytes,
        nonce: bytes
    ) -> Program:
        nonce: int = int(nonce.hex(), 16)

        chain_data_parts = []
        found = False

        for chain_data in base_data.as_iter():
            if chain_data.first() != source_chain:
                chain_data_parts.append(chain_data)
                continue

            # to add an item: treat all nonces (including [a]) as an integer list
            # add nonce, sort the list
            # then, if a == nonce1 - 1, a = nonce1, pop nonce1 - up until the first time the condition is false
            found = True
            chain_data_ints = [_.as_int() for _ in chain_data.rest().as_iter()]

            assert chain_data_ints[0] < nonce and nonce not in chain_data_ints
            chain_data_ints.append(nonce)
            chain_data_ints.sort()

            while len(chain_data_ints) > 1 and chain_data_ints[0] + 1 == chain_data_ints[1]:
                chain_data_ints[0] += 1
                chain_data_ints.pop(1)

            chain_data_parts.append(Program.to([source_chain] + chain_data_ints))

        if not found:
            if nonce == 1:
                chain_data_parts.append(Program.to([source_chain, nonce]))
            else:
                chain_data_parts.append(Program.to([source_chain, 0, nonce]))

        return Program.to(chain_data_parts)


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
        
        if self.check_already_used_chain_and_nonce(
            portal_state.used_chains_and_nonces,
            message.source_chain,
            message.nonce
        ):
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

        send_signature(message.sig.decode())


    async def messageSigner(self):
        db = self.getDb()

        while not self.syncing:
            logging.info(f"{self.chain_id.decode} message signer: Waiting to be synced before signing messages...")
            await asyncio.sleep(10)

        while True:
            messages = []
            try:
                messages = db.query(Message).filter(and_(
                    Message.destination_chain == self.chain_id,
                    Message.sig == b'',
                    Message.sig != SIG_USED_VALUE,
                )).all()
            except Exception as e:
                logging.error(f"Error querying messages: {e}", exc_info=True)
                sys.exit(1)

            for message in messages:
                try:
                    await self.signMessage(db, message)
                    db.commit()
                except Exception as e:
                    logging.error(f"Error signing message {message.nonce.hex()}: {e}", exc_info=True)
                    sys.exit(1)

            await asyncio.sleep(5)


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
            self.syncing = False
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

        prev_used_chains_and_nonces = Program.from_bytes(last_synced_portal.used_chains_and_nonces)
        if len(prev_used_chains_and_nonces.as_python()) == 0:
            prev_used_chains_and_nonces = Program.to([])
        
        chains_and_nonces = inner_solution.at("rf").as_iter() if bytes(update_package) == bytes(Program.to(0)) else []
        for cn in chains_and_nonces:
            source_chain = cn.first().as_atom()
            nonce = cn.rest().as_atom()
            prev_used_chains_and_nonces = self.add_chain_and_nonce(
                prev_used_chains_and_nonces,
                source_chain,
                nonce
            )

            msg = db.query(Message).filter(and_(
                Message.source_chain == source_chain,
                Message.nonce == nonce
            )).first()
            while msg is None:
                logging.info(f"Message {source_chain.decode()}-{nonce.hex()} not found in db; waiting 10s for other threads to catch up")
                await asyncio.sleep(10)
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

        if self.syncing:
            cr = await node.get_coin_record_by_name(new_synced_portal.coin_id)
            if cr.spent_block_index is None or cr.spent_block_index == 0:
                self.syncing = False

        if not self.syncing:
            messages = db.query(Message).filter(and_(
                Message.destination_chain == self.chain_id,
                Message.sig != SIG_USED_VALUE
            )).all()
            for message in messages:
                try:
                    _, __, ___, coin_id, ____ = decode_signature(message.sig.decode())
                    if coin_id != new_synced_portal.coin_id:
                        await self.signMessage(db, message)
                except:
                    logging.info(f"Message {self.chain}-{message.nonce.hex()}: error when decoding signature/signing - {message.sig.decode()}", exc_info=True)

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

        last_synced_portal = await self.syncPortal(db, node, last_synced_portal)

        while True:
            try:
                last_synced_portal = await self.syncPortal(db, node, last_synced_portal)
                db.commit()
            except:
                logging.error(f"{self.chain_id.decode()} portal follower: Error syncing portal coin {self.chain_id.decode()}-0x{last_synced_portal.coin_id.hex()}", exc_info=True)
                sys.exit(1)

        node.close()
        await node.await_closed()


    async def createMessageFromMemo(
            self,
            db,
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
            block_number=created_height,
            sig=b''
        )
        db.add(msg)
        db.commit()
        logging.info(f"Message {self.chain}-{nonce.hex()} added to db.")


    async def processCoinRecord(self, db: any, node: FullNodeRpcClient, coin_record: CoinRecord):
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

                    if created_ph == BRIDGING_PUZZLE_HASH and created_amount >= self.per_message_toll:
                        coin = Coin(parent_record.coin.name(), created_ph, created_amount)
                        try:
                            memo = condition.at('rrrf')
                        except:
                            logging.error(f"Coin {self.chain}-{coin.name().hex()} - error when parsing memo; skipping")
                            continue

                        try:
                            await self.createMessageFromMemo(
                                db,
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


    async def get_current_height(self, node: FullNodeRpcClient) -> int:
        try:
            return (await node.get_blockchain_state())["peak"].height
        except Exception as e:
            logging.error(f"Error getting current height for {self.chain}; sleeping 5s and making it appear everything is unconfirmed", exc_info=True)
            await asyncio.sleep(5)
            return 0


    async def messageListener(self):
        db = self.getDb()
        node = await self.getNode()

        while True:
            try:
                last_synced_height = db.query(Message.block_number).filter(
                    Message.source_chain == self.chain_id
                ).order_by(Message.block_number.desc()).first()

                if last_synced_height is None:
                    last_synced_height = get_config_item([self.chain, 'min_height'])
                else:
                    last_synced_height = last_synced_height[0]

                unfiltered_coin_records = await node.get_coin_records_by_puzzle_hash(
                    BRIDGING_PUZZLE_HASH,
                    include_spent_coins=True,
                    start_height=last_synced_height - 1
                )
                if unfiltered_coin_records is None:
                    await asyncio.sleep(30)
                    continue

                # because get_coin_records_by_puzzle_hash can be quite resource exensive, we'll process all results
                # instead of only one and calling again
                skip_coin_ids = []
                reorg = False
                while not reorg:
                    earliest_unprocessed_coin_record = None
                    for coin_record in unfiltered_coin_records:
                        nonce = coin_record.coin.name()
                        if nonce in skip_coin_ids:
                            continue

                        if coin_record.coin.amount < self.per_message_toll:
                            skip_coin_ids.append(nonce)
                            continue

                        message_in_db = db.query(Message).filter(and_(
                            Message.nonce == nonce,
                            Message.source_chain == self.chain_id
                        )).first()
                        if message_in_db is not None:
                            skip_coin_ids.append(nonce)
                            continue

                        if earliest_unprocessed_coin_record is None or coin_record.confirmed_block_index < earliest_unprocessed_coin_record.confirmed_block_index:
                            earliest_unprocessed_coin_record = coin_record

                    if earliest_unprocessed_coin_record is None:
                        break

                    # wait for this to actually be confirmed :)
                    while earliest_unprocessed_coin_record.confirmed_block_index + self.sign_min_height > (await self.get_current_height(node)):
                        await asyncio.sleep(10)

                    coin_record_copy = await node.get_coin_record_by_name(earliest_unprocessed_coin_record.coin.name())
                    if coin_record_copy is None or coin_record_copy.confirmed_block_index != earliest_unprocessed_coin_record.confirmed_block_index:
                        logging.info(f"{self.chain} message follower: Coin {self.chain}-0x{earliest_unprocessed_coin_record.coin.name().hex()}: possible reorg; re-processing")
                        reorg = True
                        break

                    await self.processCoinRecord(db, node, earliest_unprocessed_coin_record)

                    nonce = earliest_unprocessed_coin_record.coin.name()
                    skip_coin_ids.append(nonce)

                if not reorg:
                    await asyncio.sleep(30)
            except:
                logging.error(f"{self.chain_id.decode()} message listener: error", exc_info=True)
                sys.exit(1)


        node.close()
        await node.await_closed()


    def run(self, loop):
        self.loop = loop

        self.loop.create_task(self.messageSigner())
        self.loop.create_task(self.portalFollower())
        self.loop.create_task(self.messageListener())
