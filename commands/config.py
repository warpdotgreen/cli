import json
from typing import List
import click

def load_config():
    try:
        return json.loads(open('config.json', 'r').read())
    except:
        click.echo("Warning: Failed to load config.json")
        return {}

config = load_config()

def get_config_item(path: List[str]) -> any:
    current = config
    for p in path:
        current = current[p]
    return current
