# used to simulate something like FireAcademy.io
from flask import Flask, request, jsonify
from flask_cors import CORS
# from chia.util.config import load_config
from pathlib import Path
import requests
import json
import os

app = Flask(__name__)
CORS(app)

CHIA_ROOT = os.environ.get("CHIA_ROOT", os.path.expanduser("~/.chia/mainnet"))
CERT_PATH = os.path.join(CHIA_ROOT, 'config/ssl/full_node/private_full_node.crt')
KEY_PATH = os.path.join(CHIA_ROOT, 'config/ssl/full_node/private_full_node.key')

root_path = Path(CHIA_ROOT)
# config = load_config(root_path, "config.yaml")
# rpc_port = config["full_node"]["rpc_port"]
rpc_port = os.environ.get("CHIA_RPC_PORT", 8555)

RPC_URL = f'https://localhost:{rpc_port}'

WHITELIST = [
    "get_blockchain_state",
    "get_block_record",
    "get_block_record_by_height",
    "get_coin_records_by_names",
    "get_coin_records_by_parent_ids",
    "get_coin_records_by_puzzle_hash",
    "get_coin_records_by_puzzle_hashes",
    "get_coin_record_by_name",
    "get_fee_estimate",
    "get_mempool_item_by_tx_id",
    "get_mempool_items_by_coin_name",
    "get_network_info",
    "get_puzzle_and_solution",
    "get_routes",
    "healthz",
    "push_tx"
]

@app.route('/<endpoint>', methods=['GET', 'POST'])
def forward_request(endpoint):
    url = f"{RPC_URL}/{endpoint}"

    if endpoint not in WHITELIST:
        return jsonify({"error": "Endpoint not allowed"}), 403

    headers = {'Content-Type': 'application/json'}

    if request.method == 'POST':
        data = request.get_data()
        response = requests.post(url, headers=headers, data=data, cert=(CERT_PATH, KEY_PATH), verify=False)
    else:
        data = "{}"

    response = requests.post(url, headers=headers, data=data, cert=(CERT_PATH, KEY_PATH), verify=False)

    return jsonify(json.loads(response.text))

if __name__ == '__main__':
    app.run(debug=True, port=os.environ.get("PORT", 5000))
