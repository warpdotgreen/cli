from sqlalchemy import create_engine, Column, Integer, String, Boolean, ForeignKey, ARRAY
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from sqlalchemy.dialects.sqlite import BLOB

Base = declarative_base()

class Message(Base):
    __tablename__ = 'message'
    nonce = Column(BLOB, primary_key=True)
    source_chain = Column(BLOB(3), primary_key=True)
    source = Column(BLOB)
    destination_chain = Column(BLOB(3))
    destination = Column(BLOB)
    contents = Column(ARRAY(BLOB))
    block_hash = Column(BLOB)
    sig = Column(BLOB)
    used_on_destination_chain = Column(Boolean)

class Blocks(Base):
    __tablename__ = 'blocks'
    height = Column(Integer, primary_key=True)
    hash = Column(BLOB, primary_key=True)
    prev_hash = Column(BLOB, nullable=True)

def setup_database(db_path='sqlite:///data.db'):
    engine = create_engine(db_path, echo=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()

# Call setup_database() to initialize your database
# session = setup_database()
