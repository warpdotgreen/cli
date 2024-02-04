import json
from typing import List

def load_config():
    return json.loads(open('config.json', 'r').read())

config = load_config()

def get_config_item(path: List[str]) -> any:
    current = config
    for p in path:
        current = current[p]
    return current
