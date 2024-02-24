from commands.models import *
from commands.config import get_config_item
from web3 import Web3
import time
import logging
import json
import asyncio

class EthereumFollower:
    def __init__(self, chain_name: str, chain_id: bytes):
        self.chain_name = chain_name
        self.chain_id = chain_id
        self.web3 = Web3(Web3.HTTPProvider(get_config_item([chain_name, 'rpc_url'])))
        self.db = setup_database()


    def revertBlock(self, height: int):
      block = self.db.query(Block).filter(Block.height == height and Block.chain_id == self.chain_id).first()
      block_hash = block.hash
      self.db.query(Message).filter(Message.source_chain == self.chain_id and Message.block_hash == block_hash).delete()
      block.delete()
      logging.info(f"Block #{self.chain_id.decode()}-{height} reverted.")


    def addBlock(self, height: int, hash: bytes, prev_hash: bytes):
      self.db.add(Block(height=height, hash=hash, chain_id=self.chain_id, prev_hash=prev_hash))
      logging.info(f"Block #{self.chain_id.decode()}-{height} added to db.")


    # returns new block height we should sync to
    def syncBlockUsingHeight(self, height: int, block=None) -> int: 
        if block is None:
          block = self.web3.eth.get_block(height)
        logging.info(f"Processing block #{self.chain_id.decode()}-{height} with hash {block['hash'].hex()}...")

        block_height = block['number']
        assert block_height == height

        block_hash = bytes(block['hash'])
        block_prev_hash = bytes(block['parentHash'])

        prev_block = self.db.query(Block).filter(Block.height == block_height - 1 and Block.chain_id == chain_id).first()
        if prev_block is not None and prev_block.hash != block_prev_hash:
            self.revertBlock(self.db, self.chain_id, block_height - 1)
            return block_height - 1
        elif prev_block is None and block_height != get_config_item(['ethereum', 'min_height']):
            logging.info(f"Block #{self.chain_id.decode()}-{height-1} not in db - soft reverting.")
            return block_height - 1
        
        current_block = self.db.query(Block).filter(Block.height == block_height and Block.chain_id == self.chain_id).first()
        if current_block is not None and current_block.hash == block_hash and current_block.prev_hash == block_prev_hash:
            logging.info(f"Block #{self.chain_id.decode()}-{height} already in db.")
            return block_height + 1
        elif current_block is not None:
            self.revertBlock(block_height)
            return block_height
        
        self.addBlock(block_height, block_hash, block_prev_hash)
        return block_height + 1
    
    async def blockFollower(self):      
      latest_block_in_db = self.db.query(Block).filter(
         Block.chain_id == self.chain_id
      ).order_by(Block.height.desc()).first()
      latest_synced_block_height: int = latest_block_in_db.height if latest_block_in_db is not None else get_config_item([self.chain_name, 'min_height'])
      logging.info(f"Synced peak: {self.chain_id.decode()}-{latest_synced_block_height}")

      block_filter = self.web3.eth.filter('latest')

      latest_mined_block = self.web3.eth.block_number
      logging.info(f"Quickly syncing to: {self.chain_id.decode()}-{latest_mined_block}")

      while latest_synced_block_height <= latest_mined_block:
        latest_synced_block_height = self.syncBlockUsingHeight(latest_synced_block_height)
      self.db.commit()

      logging.info(f"Quick sync done on {self.chain_id.decode()}. Listening for new blocks using filter.")
      while True:
          for block_hash in block_filter.get_new_entries():
              block = self.web3.eth.get_block(block_hash)
              block_height = block['number']
              latest_synced_block_height = self.syncBlockUsingHeight(block_height, block)
              while latest_synced_block_height < block_height + 1:
                  latest_synced_block_height = self.syncBlockUsingHeight(latest_synced_block_height)
              self.db.commit()
          time.sleep(1)

    def nonceIntToBytes(self, nonceInt: int) -> bytes:
      s = hex(nonceInt)[2:]
      return (64 - len(s)) * "0" + s
    
    def addEventToDb(self, event):
      source = bytes.fromhex(event['args']['source'][2:])

      self.db.add(Message(
          nonce=event['args']['nonce'],
          source_chain=self.chain_id,
          source=b"0" * (64 - len(source)) + source,
          destination_chain=event['args']['destination_chain'],
          destination=event['args']['destination'],
          contents=join_message_contents(event['args']['contents']),
          block_hash=event['blockHash'],
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
      portal_contract_abi = json.loads(open("artifacts/contracts/Portal.sol/Portal.json", "r").read())["abi"]
      portal_contract_address = get_config_item([self.chain_name, 'portal_address'])
      
      latest_message_in_db = self.db.query(Message).filter(
         Message.source_chain == self.chain_id
      ).order_by(Message.nonce.desc()).first()
      latest_synced_nonce_int: int = int(latest_message_in_db.nonce.hex()[2:], 16) if latest_message_in_db is not None else 0
      logging.info(f"Last synced nonce: {self.chain_id.decode()}-{latest_synced_nonce_int}")

      contract = self.web3.eth.contract(address=portal_contract_address, abi=portal_contract_abi)

      event_filter = contract.events.MessageSent.create_filter(fromBlock='latest')

      last_used_nonce_int: int = contract.functions.ethNonce().call()
      logging.info(f"Quickly syncing nonce to: {self.chain_id.decode()}-{last_used_nonce_int}")

      if latest_synced_nonce_int < last_used_nonce_int:
        latest_synced_nonce_int += 1

        block_hash = latest_message_in_db.block_hash if latest_message_in_db is not None else None
        block = self.db.query(Block).filter(
           Block.hash == block_hash and Block.chain_id == self.chain_id
        ).first() if block_hash is not None else None
        query_start_height = block.height - 1 if block is not None else get_config_item([self.chain_name, 'min_height'])
        while latest_synced_nonce_int <= last_used_nonce_int:
          event = self.getEventByIntNonce(contract, latest_synced_nonce_int, query_start_height)
          self.addEventToDb(event)
          latest_synced_nonce_int += 1
        self.db.commit()

      logging.info(f"Quick sync done on {self.chain_id.decode()}. Listening for new messages using live filter.")
      while True:
          for event in event_filter.get_new_entries():
              event_nonce_int = int(event['args']['nonce'].hex(), 16)

              latest_message_in_db = self.db.query(Message).filter(
                 Message.source_chain == self.chain_id
              ).order_by(Message.nonce.desc()).first()
              latest_synced_nonce_int: int = int(latest_message_in_db.nonce.hex()[2:], 16) if latest_message_in_db is not None else 0
              
              while latest_synced_nonce_int < event_nonce_int:
                  prev_event = self.getEventByIntNonce(contract, latest_synced_nonce_int, query_start_height)
                  self.addEventToDb(prev_event)
                  latest_synced_nonce_int += 1

              self.addEventToDb(event)
              self.db.commit()
          time.sleep(1)

    async def run(self):
      self.block_follower_task = asyncio.create_task(self.blockFollower())
      self.message_follower_task = asyncio.create_task(self.messageFollower())

      await self.block_follower_task
      await self.message_follower_task
