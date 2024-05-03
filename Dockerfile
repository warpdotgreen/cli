FROM python:3.9-slim

WORKDIR /app/cli

RUN python3 -m venv venv
SHELL ["/bin/bash", "-c"]
RUN source venv/bin/activate

RUN pip install --extra-index-url https://pypi.chia.net/simple/ chia-dev-tools
RUN pip install --extra-index-url https://pypi.chia.net/simple/ chia-blockchain==2.2.0
RUN pip install web3 nostr-sdk asyncio sqlalchemy qrcode

RUN apt-get update && apt-get install -y curl
RUN curl -sL https://deb.nodesource.com/setup_18.x -o /tmp/nodesource_setup.sh
RUN chmod +x /tmp/nodesource_setup.sh && /tmp/nodesource_setup.sh

RUN apt-get install -y nodejs
RUN npm install -g npm@latest

COPY package.json /app/cli/package.json
COPY package-lock.json /app/cli/package-lock.json
COPY tsconfig.json /app/cli/tsconfig.json
RUN npm install --force

COPY hardhat.config.example.ts /app/cli/hardhat.config.ts

RUN npx hardhat compile

CMD ["python3", "cli.py"]
