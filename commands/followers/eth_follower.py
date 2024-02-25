from commands.models import *
from commands.config import get_config_item
from sqlalchemy import and_
from typing import Tuple
from web3 import Web3
import logging
import json
import asyncio

class EthereumFollower:
    chain: str
    chain_id: bytes
    sign_min_height: int
    
    def __init__(self, chain: str):
        self.chain = chain
        self.chain_id = chain.encode()
        self.sign_min_height = get_config_item([self.chain, 'sign_min_height'])


    def getDb(self):
       return setup_database()
    

    def getWeb3(self):
       return Web3(Web3.HTTPProvider(get_config_item([self.chain, 'rpc_url'])))
    

    def revertBlock(self, db, height: int):
      block = db.query(Block).filter(and_(Block.height == height, Block.chain_id == self.chain_id)).first()
      block_hash = block.hash
      db.query(Message).filter(
         and_(
            Message.source_chain == self.chain_id,
            Message.block_number >= height
          )
      ).delete()
      logging.info(f"Block #{self.chain_id.decode()}-{height} reverted.")


    def addBlock(self, db, height: int, hash: bytes, prev_hash: bytes, check_messages: bool = True):
      db.add(Block(height=height, hash=hash, chain_id=self.chain_id, prev_hash=prev_hash))
      logging.info(f"Block #{self.chain_id.decode()}-{height} added to db.")

      if check_messages:
         messages = db.query(Message).filter(and_(
            Message.source_chain == self.chain_id,
            Message.has_enough_confirmations_for_signing.is_(False),
            Message.block_number < height - self.sign_min_height
         )).all()
         for message in messages:
            message.has_enough_confirmations_for_signing = True
            logging.info(f"Message {self.chain_id.decode()}-{int(message.nonce.hex(), 16)} has enough confirmations; marked for signing.")

    # returns new block height we should sync to
    def syncBlockUsingHeight(
          self,
          db,
          web3,
          height: int,
          block = None,
          prev_block_hash: bytes = None,
          quick_sync: bool = False,
    ) -> Tuple[int, bytes]: # new_height, next prev block hash
        if block is None:
          block = web3.eth.get_block(height)
        logging.info(f"Processing block #{self.chain_id.decode()}-{height} with hash {block['hash'].hex()}...")

        block_height = block['number']
        assert block_height == height

        block_hash = bytes(block['hash'])
        block_prev_hash = bytes(block['parentHash'])

        if prev_block_hash is None:
          prev_block = db.query(Block).filter(and_(
             Block.height == block_height - 1, Block.chain_id == self.chain_id
          )).first()
          if prev_block is not None and prev_block.hash != block_prev_hash:
              prev_block_hash = prev_block.hash
              self.revertBlock(db, block_height - 1)
              return block_height - 1, None
          elif prev_block is None and block_height != get_config_item([self.chain, 'min_height']):
              logging.info(f"Block #{self.chain_id.decode()}-{height-1} not in db - soft reverting.")
              return block_height - 1, None
        else:
           if prev_block_hash != block_prev_hash:
              self.revertBlock(db, block_height - 1)
              return block_height - 1, None
        
        if not quick_sync:
          current_block = db.query(Block).filter(and_(
             Block.height == block_height and Block.chain_id == self.chain_id
          )).first()
          if current_block is not None and current_block.hash == block_hash and current_block.prev_hash == block_prev_hash:
              logging.info(f"Block #{self.chain_id.decode()}-{height} already in db.")
              return block_height + 1, None
          elif current_block is not None:
              logging.info(f"Another block #{self.chain_id.decode()}-{height} in db - reverting.")
              self.revertBlock(db, block_height)
              return block_height, None
        
        self.addBlock(db, block_height, block_hash, block_prev_hash, check_messages=not quick_sync)
        return block_height + 1, block_hash
    
    async def blockFollower(self): 
      db = self.getDb()
      web3 = self.getWeb3()

      latest_block_in_db = db.query(Block).filter(
         Block.chain_id == self.chain_id
      ).order_by(Block.height.desc()).first()
      latest_synced_block_height: int = latest_block_in_db.height if latest_block_in_db is not None else get_config_item([self.chain, 'min_height'])
      logging.info(f"Synced peak: {self.chain_id.decode()}-{latest_synced_block_height}")

      latest_mined_block = web3.eth.block_number
      logging.info(f"Quickly syncing to: {self.chain_id.decode()}-{latest_mined_block}")

      prev_block_hash = None
      iters = 0
      while latest_synced_block_height <= latest_mined_block:
        latest_synced_block_height, prev_block_hash = self.syncBlockUsingHeight(
          db, web3,
          latest_synced_block_height,
          block=None,
          prev_block_hash=prev_block_hash,
          quick_sync=prev_block_hash is not None
        )
        iters += 1
        if iters >= 200:
          logging.info(f"{self.chain_id.decode()}: over 200 iters; saving progress...")
          db.commit()
          iters = 0
          latest_mined_block = web3.eth.block_number
      db.commit()

      block_filter = web3.eth.filter('latest')

      logging.info(f"Quick sync done on {self.chain_id.decode()}. Listening for new blocks using filter.")
      while True:
          for block_hash in block_filter.get_new_entries():
              block = web3.eth.get_block(block_hash)
              block_height = block['number']
              latest_synced_block_height, _ = self.syncBlockUsingHeight(db, web3, block_height, block)
              while latest_synced_block_height < block_height + 1:
                  latest_synced_block_height, _ = self.syncBlockUsingHeight(db, web3, latest_synced_block_height)
              db.commit()
          await asyncio.sleep(5)

    def nonceIntToBytes(self, nonceInt: int) -> bytes:
      s = hex(nonceInt)[2:]
      return (64 - len(s)) * "0" + s
    
    def addEventToDb(self, db, event):
      source = bytes.fromhex(event['args']['source'][2:])
      nonce = event['args']['nonce']

      db.add(Message(
          nonce=nonce,
          source_chain=self.chain_id,
          source=b"\x00" * (64 - len(source)) + source,
          destination_chain=event['args']['destination_chain'],
          destination=event['args']['destination'],
          contents=join_message_contents(event['args']['contents']),
          block_hash=event['blockHash'],
          block_number=db.query(Block).filter(Block.hash == event['blockHash']).first().height,
          has_enough_confirmations_for_signing=False,
          sig=b'',
      ))
      logging.info(f"Message {self.chain_id.decode()}-{int(event['args']['nonce'].hex(), 16)} added to db.")

    def getEventByIntNonce(self, contract, nonce: int, start_height: int):
      one_event_filter = contract.events.MessageSent.create_filter(
          fromBlock=start_height,
          toBlock='latest',
          argument_filters={'nonce': "0x" + self.nonceIntToBytes(nonce)}
      )
      return one_event_filter.get_all_entries()[0]
    
    async def messageFollower(self):
      db = self.getDb()
      web3 = self.getWeb3()

      portal_contract_abi = json.loads(open("artifacts/contracts/Portal.sol/Portal.json", "r").read())["abi"]
      portal_contract_address = get_config_item([self.chain, 'portal_address'])
      
      latest_message_in_db = db.query(Message).filter(
         Message.source_chain == self.chain_id
      ).order_by(Message.nonce.desc()).first()
      latest_synced_nonce_int: int = int(latest_message_in_db.nonce.hex()[2:], 16) if latest_message_in_db is not None else 0
      logging.info(f"Last synced nonce: {self.chain_id.decode()}-{latest_synced_nonce_int}")

      contract = web3.eth.contract(address=portal_contract_address, abi=portal_contract_abi)

      event_filter = contract.events.MessageSent.create_filter(fromBlock='latest')

      last_used_nonce_int: int = contract.functions.ethNonce().call()
      logging.info(f"Quickly syncing nonce to: {self.chain_id.decode()}-{last_used_nonce_int}")

      query_start_height = get_config_item([self.chain, 'min_height'])
      if latest_synced_nonce_int < last_used_nonce_int:
        latest_synced_nonce_int += 1

        block_hash = latest_message_in_db.block_hash if latest_message_in_db is not None else None
        block = db.query(Block).filter(and_(
           Block.hash == block_hash and Block.chain_id == self.chain_id
        )).first() if block_hash is not None else None
        query_start_height = block.height - 1 if block is not None else get_config_item([self.chain, 'min_height'])
        while latest_synced_nonce_int <= last_used_nonce_int:
          event = self.getEventByIntNonce(contract, latest_synced_nonce_int, query_start_height)
          self.addEventToDb(db, event)
          latest_synced_nonce_int += 1
        db.commit()

      logging.info(f"Quick sync done on {self.chain_id.decode()}. Listening for new messages using live filter.")
      while True:
          for event in event_filter.get_new_entries():
              event_nonce_int = int(event['args']['nonce'].hex(), 16)

              latest_message_in_db = db.query(Message).filter(
                 Message.source_chain == self.chain_id
              ).order_by(Message.nonce.desc()).first()
              latest_synced_nonce_int: int = int(latest_message_in_db.nonce.hex()[2:], 16) if latest_message_in_db is not None else 0
              
              while latest_synced_nonce_int + 1 < event_nonce_int:
                  prev_event = self.getEventByIntNonce(contract, latest_synced_nonce_int + 1, query_start_height)
                  self.addEventToDb(db, prev_event)
                  latest_synced_nonce_int += 1

              self.addEventToDb(db, event)
              db.commit()
          await asyncio.sleep(5)

    def run(self, loop):
      self.loop = loop

      self.loop.create_task(self.blockFollower())
      self.loop.create_task(self.messageFollower())
      # todo: signer task
