import { expect } from "chai";
import { ethers } from "hardhat";
import { Bridge, BridgeMessageReceiverMock } from "../typechain-types";

describe("Bridge", function () {
    let bridge: Bridge;
    let mockReceiver: BridgeMessageReceiverMock;
    let owner: any;
    let otherAccount: any;
    const deadlineOffset = 3600; // 1 hour

    beforeEach(async function () {
        [owner, otherAccount] = await ethers.getSigners();
        const BridgeFactory = await ethers.getContractFactory("Bridge");
        bridge = await BridgeFactory.deploy();
        const MockReceiverFactory = await ethers.getContractFactory("BridgeMessageReceiverMock");
        mockReceiver = await MockReceiverFactory.deploy();
    });

    describe("Deployment", function () {
        it("Should set the right owner", async function () {
            expect(await bridge.owner()).to.equal(owner.address);
        });
    });

    describe("sendMessage", function () {
        it("Should emit a MessageSent event and increment nonce", async function () {
            const target = ethers.encodeBytes32String("target");
            const deadline = (await ethers.provider.getBlock("latest"))!.timestamp + deadlineOffset;
            await expect(bridge.sendMessage(target, true, deadline, ["0x1234"]))
                .to.emit(bridge, "MessageSent")
                .withArgs(1, target, true, deadline, ["0x1234"]);
            expect(await bridge.ethNonce()).to.equal(1);
        });

        it("Should fail if deadline is in the past", async function () {
            const target = ethers.encodeBytes32String("target");
            const deadline = (await ethers.provider.getBlock("latest"))!.timestamp - deadlineOffset;
            await expect(bridge.sendMessage(target, true, deadline, ["0x1234"]))
                .to.be.revertedWith("!deadline");
        });
    });

    describe("receiveMessage", function () {
        it("Should correctly receive and process a message", async function () {
            const nonce = 1;
            const sender = ethers.encodeBytes32String("sender");
            const deadline = (await ethers.provider.getBlock("latest"))!.timestamp + deadlineOffset;
            // await bridge.sendMessage(sender, true, deadline, ["0x1234"]);
            await expect(bridge.receiveMessage(nonce, sender, true, mockReceiver.target, deadline, "0x1234"))
                .to.not.be.reverted;
        });

        it("Should fail if nonce is already used", async function () {
            const nonce = 1;
            const sender = ethers.encodeBytes32String("sender");
            const deadline = (await ethers.provider.getBlock("latest"))!.timestamp + deadlineOffset;
            // await bridge.sendMessage(sender, true, deadline, ["0x1234"]);
            await bridge.receiveMessage(nonce, sender, true, mockReceiver.target, deadline, "0x1234");
            await expect(bridge.receiveMessage(nonce, sender, true, mockReceiver.target, deadline, "0x1234"))
                .to.be.revertedWith("!nonce");
        });

        it("Should fail if deadline has been reached", async function () {
            const nonce = 1;
            const sender = ethers.encodeBytes32String("sender");
            const futureDeadline = (await ethers.provider.getBlock("latest"))!.timestamp - deadlineOffset;
            // await bridge.sendMessage(sender, true, futureDeadline, ["0x1234"]);
            await expect(bridge.receiveMessage(nonce, sender, true, mockReceiver.target, futureDeadline, "0x1234"))
                .to.be.revertedWith("!deadline");
        });

        it("Should fail if called by non-owner", async function () {
            const nonce = 1;
            const sender = ethers.encodeBytes32String("sender");
            const deadline = (await ethers.provider.getBlock("latest"))!.timestamp + deadlineOffset;
            // await bridge.sendMessage(sender, true, deadline, ["0x1234"]);
            await expect(bridge.connect(otherAccount).receiveMessage(nonce, sender, true, mockReceiver.target, deadline, "0x1234"))
                .to.be.revertedWithCustomError(bridge, "OwnableUnauthorizedAccount");
        });
    });
});
