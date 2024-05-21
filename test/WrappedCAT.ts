import { expect } from "chai";
import { ethers } from "hardhat";
import { Portal, WrappedCAT } from "../typechain-types";
import { HardhatEthersSigner } from "@nomicfoundation/hardhat-ethers/signers";
import { getPrivateKeys, getSig } from "./Portal";

const xchChain = "0x786368";
const receiverPh = ethers.encodeBytes32String("receiver-puzzle-hash");

describe("WrappedCAT", function () {
    let portal: Portal;
    let wrappedCAT: WrappedCAT;
    let owner: HardhatEthersSigner;
    let user: HardhatEthersSigner;
    let signer: HardhatEthersSigner;
    let signerSk: string;
    let otherChain = "0x786368"; // "xch"
    let invalidOtherChain = "0x747374"; // "tst"
    // /\ supported, but invalid in the sense that it's not the message source
    
    const messageToll = ethers.parseEther("0.001");
    const tip = 30n; // 0.3%
    const chiaToERC20AmountFactor = 10n ** 15n; // CATs have 3 decimals, 18 - 3 = 15
    const nonce1 = ethers.encodeBytes32String("nonce1");
    const lockerPuzzleHash = ethers.encodeBytes32String("locker-puzzle-hash");
    const unlockerPuzzleHash = ethers.encodeBytes32String("unlocker-puzzle-hash");

    beforeEach(async function () {
        [owner, user, signer] = await ethers.getSigners();
        const [_, __, _signerSk] = getPrivateKeys(3);
        signerSk = _signerSk;

        const PortalFactory = await ethers.getContractFactory("Portal");
        portal = await PortalFactory.deploy();
        await portal.initialize(owner.address, messageToll, [ signer.address ], 1, [ xchChain, invalidOtherChain ]);

        const WrappedCATFactory = await ethers.getContractFactory("WrappedCAT");
        wrappedCAT = await WrappedCATFactory.deploy(
            "Wrapped CAT", "wCAT", portal.target, tip, chiaToERC20AmountFactor, otherChain
        );

        await wrappedCAT.initializePuzzleHashes(lockerPuzzleHash, unlockerPuzzleHash);
    });

    describe("Deployment", function () {
        it("Should have correct initial values", async function () {
            expect(await wrappedCAT.tip()).to.equal(tip);
            expect(await wrappedCAT.portal()).to.equal(portal.target);
            expect(await wrappedCAT.mojoToTokenRatio()).to.equal(chiaToERC20AmountFactor);
            expect(await wrappedCAT.otherChain()).to.equal(otherChain);
            expect(await wrappedCAT.lockerPuzzleHash()).to.equal(lockerPuzzleHash);
            expect(await wrappedCAT.unlockerPuzzleHash()).to.equal(unlockerPuzzleHash);
        });

        it("Should not allow setting puzzles a second time", async function () {
            await expect(wrappedCAT.initializePuzzleHashes(lockerPuzzleHash, unlockerPuzzleHash))
                .to.be.revertedWith("nope");
        });

        it("Should not allow a tip that is too high", async function () {
            const WrappedCATFactory = await ethers.getContractFactory("WrappedCAT");
            const invalidTip = 10001n; // 100.01%

            await expect(
                WrappedCATFactory.deploy(
                    "Wrapped CAT", "wCAT", portal.target, invalidTip, chiaToERC20AmountFactor, otherChain
                )
            ).to.be.revertedWith("!tip");
        });

        it("Should not allow a tip that is too low", async function () {
            const WrappedCATFactory = await ethers.getContractFactory("WrappedCAT");
            const invalidTip = 0; // 0%

            await expect(
                WrappedCATFactory.deploy(
                    "Wrapped CAT", "wCAT", portal.target, invalidTip, chiaToERC20AmountFactor, otherChain
                )
            ).to.be.revertedWith("!tip");
        });

        it("Should not allow a portal address equal to addres(0)", async function () {
            const WrappedCATFactory = await ethers.getContractFactory("WrappedCAT");

            await expect(
                WrappedCATFactory.deploy(
                    "Wrapped CAT", "wCAT", ethers.ZeroAddress, tip, chiaToERC20AmountFactor, otherChain
                )
            ).to.be.revertedWith("!portal");
        });
    });

    describe("receiveMessage", function () {
        it("Should mint tokens correctly", async function () {
            const receiver = user.address;
            const amount = ethers.parseUnits("10", 3);
            const message = [
                ethers.zeroPadValue(receiver, 32),
                ethers.zeroPadValue("0x" + amount.toString(16), 32)
            ]
            const expectedTip = amount * chiaToERC20AmountFactor * tip / 10000n;

            const sig = await getSig(
                portal,
                nonce1, otherChain, lockerPuzzleHash, wrappedCAT.target.toString(), message,
                [signer],
                [signerSk]
            );

            await expect(
                portal.receiveMessage(
                    nonce1, otherChain, lockerPuzzleHash, wrappedCAT.target, message, sig
                )
            ).to.changeTokenBalances(
                wrappedCAT,
                [receiver, portal],
                [amount * chiaToERC20AmountFactor - expectedTip, expectedTip]
            );
        });

        it("Should fail if source is wrong", async function () {
            const receiver = user.address;
            const amount = ethers.parseUnits("10", 3);
            const message = [
                ethers.zeroPadValue(receiver, 32),
                ethers.zeroPadValue("0x" + amount.toString(16), 32)
            ]

            const sig = await getSig(
                portal,
                nonce1, otherChain, unlockerPuzzleHash, wrappedCAT.target.toString(), message,
                [signer],
                [signerSk]
            );

            await expect(
                portal.receiveMessage(
                    nonce1, otherChain, unlockerPuzzleHash, wrappedCAT.target, message, sig
                )
            ).to.be.revertedWith("!msg");
        });

        it("Should fail for 0-amount mints", async function () {
            const receiver = user.address;
            const message = [
                ethers.zeroPadValue(receiver, 32),
                "0x" + '00'.repeat(32),
            ]

            const sig = await getSig(
                portal,
                nonce1, otherChain, lockerPuzzleHash, wrappedCAT.target.toString(), message,
                [signer],
                [signerSk]
            );

            await expect(
                portal.receiveMessage(
                    nonce1, otherChain, lockerPuzzleHash, wrappedCAT.target, message, sig
                )
            ).to.be.revertedWith("!amnt");
        });

        it("Should fail if source chain is wrong", async function () {
            const receiver = user.address;
            const amount = ethers.parseUnits("10", 3);
            const message = [
                ethers.zeroPadValue(receiver, 32),
                ethers.zeroPadValue("0x" + amount.toString(16), 32)
            ]

            const sig = await getSig(
                portal,
                nonce1, invalidOtherChain, lockerPuzzleHash, wrappedCAT.target.toString(), message,
                [signer],
                [signerSk]
            );

            await expect(
                portal.receiveMessage(
                    nonce1, invalidOtherChain, lockerPuzzleHash, wrappedCAT.target, message, sig
                )
            ).to.be.revertedWith("!msg");
        });

        it("Should fail if msg.sender is not portal", async function () {
            const receiver = user.address;
            const amount = ethers.parseUnits("10", 3);
            const message = [
                ethers.zeroPadValue(receiver, 32),
                ethers.zeroPadValue("0x" + amount.toString(16), 32)
            ]

            await expect(
                wrappedCAT.receiveMessage(
                    nonce1, otherChain, lockerPuzzleHash, message
                )
            ).to.be.revertedWith("!msg");
        });
    });

    describe("bridgeBack", function () {
        this.beforeEach(async function () {
            const receiver = user.address;
            const amount = ethers.parseUnits("10", 3);
            const message = [
                ethers.zeroPadValue(receiver, 32),
                ethers.zeroPadValue("0x" + amount.toString(16), 32)
            ]
            const expectedTip = amount * chiaToERC20AmountFactor * tip / 10000n;

            const sig = await getSig(
                portal,
                nonce1, otherChain, lockerPuzzleHash, wrappedCAT.target.toString(), message,
                [signer],
                [signerSk]
            );

            await expect(
                portal.receiveMessage(
                    nonce1, otherChain, lockerPuzzleHash, wrappedCAT.target, message, sig
                )
            ).to.changeTokenBalances(
                wrappedCAT,
                [receiver, portal],
                [amount * chiaToERC20AmountFactor - expectedTip, expectedTip]
            );
        });

        it("Should correctly burn tokens, send tip, and send a message", async function () {
            const amountToBridgeBackMojo = ethers.parseUnits("5", 3);
            const expectedTipMojo = amountToBridgeBackMojo * tip / 10000n;

            const tx = await wrappedCAT.connect(user).bridgeBack(
                receiverPh,
                amountToBridgeBackMojo,
                { value: await portal.messageToll() }
            );

            await expect(tx).to.emit(portal, "MessageSent")
                .withArgs(
                    "0x0000000000000000000000000000000000000000000000000000000000000001",
                    wrappedCAT.target,
                    xchChain,
                    unlockerPuzzleHash,
                    [
                        ethers.zeroPadValue(receiverPh, 32),
                        ethers.zeroPadValue("0x" + (amountToBridgeBackMojo - expectedTipMojo).toString(16), 32)
                    ]
                );
            
            await expect(tx).to.changeTokenBalances(
                wrappedCAT,
                [user, portal],
                [-amountToBridgeBackMojo * chiaToERC20AmountFactor, expectedTipMojo * chiaToERC20AmountFactor]
            );
        });

        it("Should have a minimum tip of 1 mojo", async function () {
            const amountToBridgeBackMojo = 333n;
            const expectedTipMojo = 1n;

            const tx = await wrappedCAT.connect(user).bridgeBack(
                receiverPh,
                amountToBridgeBackMojo,
                { value: await portal.messageToll() }
            );

            await expect(tx).to.emit(portal, "MessageSent")
                .withArgs(
                    "0x0000000000000000000000000000000000000000000000000000000000000001",
                    wrappedCAT.target,
                    xchChain,
                    unlockerPuzzleHash,
                    [
                        ethers.zeroPadValue(receiverPh, 32),
                        ethers.zeroPadValue("0x0" + (amountToBridgeBackMojo - expectedTipMojo).toString(16), 32)
                    ]
                );

            await expect(tx).to.changeTokenBalances(
                wrappedCAT,
                [user, portal],
                [-amountToBridgeBackMojo * chiaToERC20AmountFactor, expectedTipMojo * chiaToERC20AmountFactor]
            );
        });

        it("Should revert if incorrect message toll is used", async function () {
            const amountToBridgeBackMojo = ethers.parseUnits("5", 3);

            await expect(
                wrappedCAT.connect(user).bridgeBack(
                    receiverPh,
                    amountToBridgeBackMojo,
                    { value: ((await portal.messageToll()) / 10n) }
                )
            ).to.be.revertedWith("!toll");
        });

        it("Should revert if amount=0", async function () {
            const amountToBridgeBackMojo = 0n;

            await expect(
                wrappedCAT.connect(user).bridgeBack(
                    receiverPh,
                    amountToBridgeBackMojo,
                    { value: (await portal.messageToll()) }
                )
            ).to.be.revertedWith("!amnt");
        });

        it("Should revert if tip=amount, causing bridged amount to be 0", async function () {
            const amountToBridgeBackMojo = 1n;
            
            await expect(
                wrappedCAT.connect(user).bridgeBack(
                    receiverPh,
                    amountToBridgeBackMojo,
                    { value: (await portal.messageToll()) }
                )
            ).to.be.revertedWith("!amnt");
        });
    });
});
