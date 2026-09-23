import json
from typing import List, Tuple
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


def load_listener_config(cfg: dict = None):
    from commands.control_channel import load_control_config
    from commands.spend_policy import BridgeRoutes
    cfg = config if cfg is None else cfg
    control = load_control_config(cfg)
    routes = BridgeRoutes.from_config(cfg)
    return routes, control
