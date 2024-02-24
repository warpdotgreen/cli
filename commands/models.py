from sqlalchemy import create_engine, Column, Integer, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.dialects.sqlite import BLOB
from typing import List

Base = declarative_base()

def join_message_contents(contents: List[bytes]) -> bytes:
    return b''.join(contents)

def split_message_contents(contents: bytes) -> List[bytes]:
    return [contents[i:i+32] for i in range(0, len(contents), 32)]

class Message(Base):
    __tablename__ = 'messages'
    nonce = Column(BLOB, primary_key=True)
    source_chain = Column(BLOB(3), primary_key=True)
    source = Column(BLOB)
    destination_chain = Column(BLOB(3))
    destination = Column(BLOB)
    contents = Column(BLOB)
    block_hash = Column(BLOB)
    sig = Column(BLOB)

class Block(Base):
    __tablename__ = 'blocks'
    chain_id = Column(BLOB(3), primary_key=True)
    height = Column(Integer, primary_key=True)
    hash = Column(BLOB)
    prev_hash = Column(BLOB)

class Portal(Base):
    __tablename__ = 'portal_states'
    chain_id = Column(BLOB(3), primary_key=True)
    coin_id = Column(BLOB(32), primary_key=True)
    used_chains_and_nonces = Column(BLOB)

def setup_database(db_path='sqlite:///data.db'):
    engine = create_engine(db_path, echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()

# Call setup_database() to initialize your database
# session = setup_database()
