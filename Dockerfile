FROM python:3.12-slim

WORKDIR /app

RUN python3 -m venv venv
SHELL ["/bin/bash", "-c"]
RUN source venv/bin/activate

RUN apt-get update && apt-get install -y curl gcc build-essential
RUN curl -sL https://deb.nodesource.com/setup_18.x -o /tmp/nodesource_setup.sh
RUN chmod +x /tmp/nodesource_setup.sh && /tmp/nodesource_setup.sh

RUN apt-get install -y nodejs
RUN npm install -g npm@latest

RUN pip install --extra-index-url https://pypi.chia.net/simple/ chia-dev-tools==1.2.6

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY package.json .
COPY package-lock.json .
COPY tsconfig.json .
RUN npm i

COPY puzzles puzzles/
COPY include include/
COPY contracts contracts/
COPY l1_block_abi.json .
COPY test.sh .
COPY scripts scripts/
COPY drivers drivers/
COPY commands commands/
COPY test test/
COPY tests tests/
COPY cli.py .

COPY hardhat.config.example.ts /app/hardhat.config.ts
RUN npx hardhat compile

ENTRYPOINT ["python3", "cli.py"]
