# used to simulate something like FireAcademy.io
from flask import Flask, request, jsonify
import requests
import json

app = Flask(__name__)

CERT_PATH = '~/.chia/mainnet/config/ssl/full_node/private_full_node.crt'
KEY_PATH = '~/.chia/mainnet/config/ssl/full_node/private_full_node.key'
RPC_URL = 'https://localhost:8555'

@app.route('/<endpoint>', methods=['GET', 'POST'])
def forward_request(endpoint):
    url = f"{RPC_URL}/{endpoint}"
    headers = {'Content-Type': 'application/json'}

    if request.method == 'POST':
        data = request.get_data()
        response = requests.post(url, headers=headers, data=data, cert=(CERT_PATH, KEY_PATH), verify=False)
    else:
        response = requests.get(url, headers=headers, cert=(CERT_PATH, KEY_PATH), verify=False)

    return jsonify(json.loads(response.text))

if __name__ == '__main__':
    app.run(debug=True, port=5000)
