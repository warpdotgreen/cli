import { expect } from "chai";
import { ethers, config } from "hardhat";
import { Portal, PortalMessageReceiverMock } from "../typechain-types";
import { HardhatEthersSigner } from "@nomicfoundation/hardhat-ethers/signers";
import {
  signTypedData,
  SignTypedDataVersion,
  TypedMessage,
} from "@metamask/eth-sig-util";
import { HardhatNetworkHDAccountsConfig } from "hardhat/types";

export async function getSig(
    portal: Portal,
    nonce: string,
    source_chain: string,
    source: string,
    destination: string,
    message: string[],
    signers: any[],
    privateKeys: string[]
): Promise<any> {
    const data: TypedMessage<any> = {
      types: {
        EIP712Domain: [
          { name: "name", type: "string" },
          { name: "version", type: "string" },
          { name: "chainId", type: "uint256" },
          { name: "verifyingContract", type: "address" },
        ],
        Message: [
          { name: "nonce", type: "bytes32" },
          { name: "source_chain", type: "bytes3" },
          { name: "source", type: "bytes32" },
          { name: "destination", type: "address" },
          { name: "contents", type: "bytes32[]" },
        ],
      },
      domain: {
        name: "warp.green Portal",
        version: "1",
        chainId: parseInt((await ethers.provider.getNetwork()).chainId.toString()),
        verifyingContract: portal.target.toString(),
      },
      primaryType: "Message",
      message: {
        nonce,
        source_chain,
        source,
        destination,
        contents: message,
      },
    };
    
    const signersAndPks = signers
        .map((signer, i) => [signer, privateKeys[i]])
        .sort((a, b) => a[0].address.localeCompare(b[0].address));

    let signatures = [];
    for (let i = 0; i < signersAndPks.length; i++) {
        const signedMsg = signTypedData({
            privateKey: Buffer.from(signersAndPks[i][1].slice(2), 'hex'),
            data,
            version: SignTypedDataVersion.V4,
        });
        const sig = ethers.Signature.from(signedMsg);
        signatures.push(ethers.concat(['0x' + sig.v.toString(16), sig.r, sig.s]));
    }

    return ethers.concat(signatures);
}

export function getPrivateKeys(limit: number): string[] {
    const accounts = config.networks.hardhat.accounts as HardhatNetworkHDAccountsConfig;
    const mnemonic = accounts.mnemonic;

    let pks: string[] = [];
    for(let i = 0; i < limit; i++) {
        const wallet = ethers.HDNodeWallet.fromMnemonic(
            ethers.Mnemonic.fromPhrase(mnemonic), `${accounts.path}/${i}`);
        pks.push(wallet.privateKey);
    }

    return pks;
}

describe("Portal", function () {
    let portal: Portal;
    let mockReceiver: PortalMessageReceiverMock;
    let owner: HardhatEthersSigner;
    let user: HardhatEthersSigner;
    let messageToll: any;
    let signer1: HardhatEthersSigner;
    let signer2: HardhatEthersSigner;
    let signer3: HardhatEthersSigner;
    let signer1Sk: string;
    let signer2Sk: string;
    let signer3Sk: string;
    let userSk: string;
    let ownerSk: string;

    const nonce = ethers.encodeBytes32String("nonce1");
    const puzzleHash = ethers.encodeBytes32String("puzzleHash");
    const xchChain = "0x786368";
    const nonSupportedChain = "0x6e6f6e"; // b"non".hex()
    const message = [
        ethers.encodeBytes32String("message-part-1"),
        ethers.encodeBytes32String("message-part-2")
    ]

    beforeEach(async function () {
        [user, owner, signer1, signer2, signer3] = await ethers.getSigners();
        [userSk, ownerSk, signer1Sk, signer2Sk, signer3Sk] = getPrivateKeys(5);

        messageToll = ethers.parseEther("0.01");
        const PortalFactory = await ethers.getContractFactory("Portal");
        portal = await PortalFactory.deploy();
        await portal.initialize(
            owner.address,
            messageToll,
            [signer1.address, signer2.address, signer3.address],
            2,
            [ xchChain ]
        );
        const MockReceiverFactory = await ethers.getContractFactory("PortalMessageReceiverMock");
        mockReceiver = await MockReceiverFactory.deploy();
    });

    describe("Deployment", function () {
        it("Should set the right parameters", async function () {
            expect(await portal.owner()).to.equal(owner.address);
            expect(await portal.messageToll()).to.equal(messageToll);
            expect(await portal.signatureThreshold()).to.equal(2);
            expect(await portal.isSigner(signer1.address)).to.be.true;
            expect(await portal.isSigner(signer2.address)).to.be.true;
            expect(await portal.isSigner(signer3.address)).to.be.true;
        });

        it("Should only run the 'initialize' function once without reverting", async function () {
            await expect(
                portal.initialize(
                    owner.address,
                    messageToll,
                    [signer1.address, signer2.address, signer3.address],
                    2,
                    [ xchChain ]
                )
            ).to.be.revertedWithCustomError(portal, "InvalidInitialization");
        });

        it("Should not allow one of the signers to be the zero address", async function () {
            const PortalFactory = await ethers.getContractFactory("Portal");
            const newPortal = await PortalFactory.deploy();
            await expect(
                newPortal.initialize(
                    owner.address,
                    messageToll,
                    [signer1.address, signer2.address, ethers.ZeroAddress, signer3.address],
                    2,
                    [ xchChain ]
                )
            ).to.be.revertedWith("!signer");
        });
    });

    describe("sendMessage", function () {
        it("Should emit MessageSent and increment nonce", async function () {
            await expect(
                    portal.sendMessage(
                        xchChain,
                        puzzleHash,
                        message,
                        { value: messageToll }
                    ))
                .to.emit(portal, "MessageSent")
                .withArgs(
                    "0x0000000000000000000000000000000000000000000000000000000000000001",
                    user.address,
                    xchChain,
                    puzzleHash,
                    message
                );
            expect(await portal.ethNonce()).to.equal(1);
        });

        it("Should fail if message toll is not given", async function () {
            await expect(portal.sendMessage(xchChain, puzzleHash, message, {value: messageToll - 1n}))
                .to.be.revertedWith("!toll");
        });

        it("Should fail if message destination is not supported", async function () {
            await expect(portal.sendMessage(nonSupportedChain, puzzleHash, message, {value: messageToll}))
                .to.be.revertedWith("!dest");
        });

        it("Should fail if message toll is incorrect", async function () {
            await expect(portal.sendMessage(xchChain, puzzleHash, message))
                .to.be.revertedWith("!toll");
        });

        it("Should fail if block.coinbase rejects payment", async function () {
            const UncallableFactory = await ethers.getContractFactory("Uncallable");
            const uncallable = await UncallableFactory.deploy(portal.target);

            await ethers.provider.send("hardhat_setCoinbase", [
                uncallable.target,
            ]);

            await expect(
                    portal.sendMessage(
                        xchChain,
                        puzzleHash,
                        message,
                        { value: messageToll }
                    )
            ).to.be.revertedWith("!toll");

            // test cleanup
            // https://hardhat.org/hardhat-network/docs/reference#coinbase
            await ethers.provider.send("hardhat_setCoinbase", [
                "0xc014ba5ec014ba5ec014ba5ec014ba5ec014ba5e",
            ]);
        });
    });

    describe("receiveMessage", function () {
        it("Should process valid message and emit event", async function () {
            const sig = await getSig(
                portal, nonce, xchChain, puzzleHash, mockReceiver.target.toString(), message,
                [signer1, signer2],
                [signer1Sk, signer2Sk]
            );
            await expect(
                await portal.receiveMessage(nonce, xchChain, puzzleHash, mockReceiver.target, message, sig)
            ).to.emit(portal, "MessageReceived").withArgs(
                nonce,
                xchChain,
                puzzleHash,
                mockReceiver.target,
                message
            )
        });

        it("Should fail if same nonce is used twice", async function () {
            const sig = await getSig(
                portal, nonce, xchChain, puzzleHash, mockReceiver.target.toString(), message,
                [signer2, signer3],
                [signer2Sk, signer3Sk]
            );
            await portal.receiveMessage(nonce, xchChain, puzzleHash, mockReceiver.target, message, sig);
            await expect(portal.receiveMessage(nonce, xchChain, puzzleHash, mockReceiver.target, message, sig))
                .to.be.revertedWith("!nonce");
        });

        it("Should fail is message is received from unsupported chain", async function () {
            const sig = await getSig(
                portal, nonce, nonSupportedChain, puzzleHash, mockReceiver.target.toString(), message,
                [signer2, signer3],
                [signer2Sk, signer3Sk]
            );
            await expect(portal.receiveMessage(nonce, nonSupportedChain, puzzleHash, mockReceiver.target, message, sig))
                .to.be.revertedWith("!src");
        });

        it("Should fail is same sig is used twice", async function () {
            let sig = await getSig(
                portal, nonce, xchChain, puzzleHash, mockReceiver.target.toString(), message,
                [signer2, signer2],
                [signer2Sk, signer2Sk]
            );
            await expect(portal.receiveMessage(nonce, xchChain, puzzleHash, mockReceiver.target, message, sig))
                .to.be.revertedWith("!order");
        });

        it("Should fail is signature does not belong to a signer", async function () {
            let sig = await getSig(
                portal, nonce, xchChain, puzzleHash, mockReceiver.target.toString(), message,
                [user, signer2],
                [userSk, signer2Sk]
            );
            await expect(portal.receiveMessage(nonce, xchChain, puzzleHash, mockReceiver.target, message, sig))
                .to.be.revertedWith("!signer");
        });

        it("Should fail is not enough sigs are used", async function () {
            let sig = await getSig(
                portal, nonce, xchChain, puzzleHash, mockReceiver.target.toString(), message,
                [signer1],
                [signer1Sk]
            );
            await expect(portal.receiveMessage(nonce, xchChain, puzzleHash, mockReceiver.target, message, sig))
                .to.be.revertedWith("!len");
        });
    });

    describe("withdrawEther", function () {
        it("Should allow owner to withdraw", async function () {
            const amount = ethers.parseEther("0.01");
            await owner.sendTransaction({ to: portal.target, value: amount });
            await expect(portal.connect(owner).rescueEther([signer1.address], [amount]))
                .to.changeEtherBalances([portal, signer1], [-amount, amount]);
        });

        it("Should allow withdrawal to multiple addresses", async function () {
            const amount1 = ethers.parseEther("0.002");
            const amount2 = ethers.parseEther("0.004");
            const amount3 = ethers.parseEther("0.004");
            await owner.sendTransaction({ to: portal.target, value: amount1 + amount2 + amount3 });
            await expect(portal.connect(owner).rescueEther(
                [signer1.address, signer2.address, signer3.address],
                [amount1, amount2, amount3])
            ).to.changeEtherBalances(
                [portal, signer1, signer2, signer3],
                [-messageToll, amount1, amount2, amount3]
            );
        });

        it("Should fail if non-owner tries to withdraw", async function () {
            const amount = ethers.parseEther("0.01");
            await expect(portal.rescueEther([user.address], [amount]))
                .to.be.revertedWithCustomError(portal, "OwnableUnauthorizedAccount");
        });
    });

    describe("rescueAsset", function () {
        let erc20: any;

        beforeEach(async function () {
            const ERC20Factory = await ethers.getContractFactory("ERC20Mock", owner);
            erc20 = await ERC20Factory.deploy("Mock ERC20", "MERC20", 18);
            await erc20.mint(portal.target, ethers.parseEther("300"));
        });

        it("Should allow owner to withdraw", async function () {
            const amount = ethers.parseEther("0.01");
            await portal.connect(owner).sendMessage(xchChain, puzzleHash, message, { value: messageToll });
            await expect(portal.connect(owner).rescueAsset(erc20.target, [signer1.address], [amount]))
                .to.changeTokenBalances(erc20, [portal, signer1], [-amount, amount]);
        });

        it("Should allow withdrawal to multiple addresses", async function () {
            const amount1 = ethers.parseEther("0.002");
            const amount2 = ethers.parseEther("0.004");
            const amount3 = ethers.parseEther("0.004");
            await portal.connect(owner).sendMessage(xchChain, puzzleHash, message, { value: messageToll });
            await expect(portal.connect(owner).rescueAsset(
                erc20.target,
                [signer1.address, signer2.address, signer3.address],
                [amount1, amount2, amount3])
            ).to.changeTokenBalances(
                erc20,
                [portal, signer1, signer2, signer3],
                [-messageToll, amount1, amount2, amount3]
            );
        });

        it("Should fail if non-owner tries to withdraw", async function () {
            const amount = ethers.parseEther("0.01");
            await expect(portal.rescueAsset(erc20, [user.address], [amount]))
                .to.be.revertedWithCustomError(portal, "OwnableUnauthorizedAccount");
        });
    });

    describe("updateSigner", function () {
        it("Should allow owner to add signer", async function () {
            await expect(portal.connect(owner).updateSigner(user.address, true))
                .to.emit(portal, "SignerUpdated").withArgs(user.address, true);
            expect(await portal.isSigner(user.address)).to.be.true;
        });

        it("Should allow owner to remove signer", async function () {
            await expect(portal.connect(owner).updateSigner(signer1.address, false))
                .to.emit(portal, "SignerUpdated").withArgs(signer1.address, false);
            expect(await portal.isSigner(signer1.address)).to.be.false;
        });

        it("Should not allow non-owner to update signer", async function () {
            await expect(
                portal.updateSigner(user.address, true)
            ).to.be.revertedWithCustomError(portal, "OwnableUnauthorizedAccount");
            expect(await portal.isSigner(user.address)).to.be.false;
        });

        it("Should fail if value is already set to new value", async function () {
            await expect(
                portal.connect(owner).updateSigner(signer1.address, true)
            ).to.be.revertedWith("!diff");
            expect(await portal.isSigner(signer1.address)).to.be.true;
        });

        it("Should fail if signer is the zero address", async function () {
            await expect(
                portal.connect(owner).updateSigner(ethers.ZeroAddress, true)
            ).to.be.revertedWith("!signer");
        });
    });

    describe("updateSignatureThreshold", function () {
        it("Should allow owner to update threshold", async function () {
            await expect(portal.connect(owner).updateSignatureThreshold(1))
                .to.emit(portal, "SignagtureThresholdUpdated").withArgs(1);
            expect(await portal.signatureThreshold()).to.equal(1);
        });

        it("Should not allow non-owner to update thresholg", async function () {
            await expect(
                portal.updateSignatureThreshold(1)
            ).to.be.revertedWithCustomError(portal, "OwnableUnauthorizedAccount");
            expect(await portal.signatureThreshold()).to.equal(2);
        });

        it("Should fail if value is already set to new value", async function () {
            await expect(
                portal.connect(owner).updateSignatureThreshold(2)
            ).to.be.revertedWith("!val");
            expect(await portal.signatureThreshold()).to.equal(2);
        });
    });

    describe("updateMessageToll", function () {
        it("Should allow owner to update the per-message toll", async function () {
            await expect(portal.connect(owner).updateMessageToll(messageToll * 2n))
                .to.emit(portal, "MessageTollUpdated").withArgs(messageToll * 2n);
            expect(await portal.messageToll()).to.equal(messageToll * 2n);
        });

        it("Should not allow non-owner to update per-message toll", async function () {
            await expect(
                portal.updateMessageToll(messageToll * 2n)
            ).to.be.revertedWithCustomError(portal, "OwnableUnauthorizedAccount");
            expect(await portal.messageToll()).to.equal(messageToll);
        });

        it("Should fail if value is already set to new value", async function () {
            await expect(
                portal.connect(owner).updateMessageToll(messageToll)
            ).to.be.revertedWith("!diff");
            expect(await portal.messageToll()).to.equal(messageToll);
        });
    });

    describe("updateSupportedChain", function () {
        it("Should allow owner to add supported chain", async function () {
            await expect(portal.connect(owner).updateSupportedChain(nonSupportedChain, true))
                .to.emit(portal, "SupportedChainUpdated").withArgs(nonSupportedChain, true);
            expect(await portal.supportedChains(nonSupportedChain)).to.be.true;
        });

        it("Should allow owner to remove supported chains", async function () {
            await expect(portal.connect(owner).updateSupportedChain(xchChain, false))
                .to.emit(portal, "SupportedChainUpdated").withArgs(xchChain, false);
            expect(await portal.supportedChains(nonSupportedChain)).to.be.false;
        });

        it("Should not allow non-owner to update supported chains", async function () {
            await expect(
                portal.updateSupportedChain(xchChain, false)
            ).to.be.revertedWithCustomError(portal, "OwnableUnauthorizedAccount");
            expect(await portal.supportedChains(xchChain)).to.be.true;
        });

        it("Should fail if current value is already set to new value", async function () {
            await expect(
                portal.connect(owner).updateSupportedChain(xchChain, true)
            ).to.be.revertedWith("!diff");
            expect(await portal.supportedChains(xchChain)).to.be.true;
        });
    });
});
