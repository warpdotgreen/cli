import { expect } from "chai";
import { ethers } from "hardhat";
import { Portal, PortalMessageReceiverMock } from "../typechain-types";

describe("Portal", function () {
    let portal: Portal;
    let mockReceiver: PortalMessageReceiverMock;
    let owner: any;
    let otherAccount: any;
    const deadlineOffset = 3600; // 1 hour

    beforeEach(async function () {
        [owner, otherAccount] = await ethers.getSigners();
        const PortalFactory = await ethers.getContractFactory("Portal");
        portal = await PortalFactory.deploy();
        const MockReceiverFactory = await ethers.getContractFactory("PortalMessageReceiverMock");
        mockReceiver = await MockReceiverFactory.deploy();
    });

    describe("Deployment", function () {
        it("Should set the right owner", async function () {
            expect(await portal.owner()).to.equal(owner.address);
        });
    });

    describe("sendMessage", function () {
        it("Should emit a MessageSent event and increment nonce", async function () {
            const target = ethers.encodeBytes32String("target");
            const deadline = (await ethers.provider.getBlock("latest"))!.timestamp + deadlineOffset;
            await expect(portal.sendMessage(target, true, deadline, ["0x1234"]))
                .to.emit(portal, "MessageSent")
                .withArgs(1, target, true, deadline, ["0x1234"]);
            expect(await portal.ethNonce()).to.equal(1);
        });

        it("Should fail if deadline is in the past", async function () {
            const target = ethers.encodeBytes32String("target");
            const deadline = (await ethers.provider.getBlock("latest"))!.timestamp - deadlineOffset;
            await expect(portal.sendMessage(target, true, deadline, ["0x1234"]))
                .to.be.revertedWith("!deadline");
        });
    });

    describe("receiveMessage", function () {
        it("Should correctly receive and process a message", async function () {
            const nonce = 1;
            const sender = ethers.encodeBytes32String("sender");
            const deadline = (await ethers.provider.getBlock("latest"))!.timestamp + deadlineOffset;
            // await portal.sendMessage(sender, true, deadline, ["0x1234"]);
            await expect(portal.receiveMessage(nonce, sender, true, mockReceiver.target, deadline, "0x1234"))
                .to.not.be.reverted;
        });

        it("Should fail if nonce is already used", async function () {
            const nonce = 1;
            const sender = ethers.encodeBytes32String("sender");
            const deadline = (await ethers.provider.getBlock("latest"))!.timestamp + deadlineOffset;
            // await portal.sendMessage(sender, true, deadline, ["0x1234"]);
            await portal.receiveMessage(nonce, sender, true, mockReceiver.target, deadline, "0x1234");
            await expect(portal.receiveMessage(nonce, sender, true, mockReceiver.target, deadline, "0x1234"))
                .to.be.revertedWith("!nonce");
        });

        it("Should fail if deadline has been reached", async function () {
            const nonce = 1;
            const sender = ethers.encodeBytes32String("sender");
            const futureDeadline = (await ethers.provider.getBlock("latest"))!.timestamp - deadlineOffset;
            // await portal.sendMessage(sender, true, futureDeadline, ["0x1234"]);
            await expect(portal.receiveMessage(nonce, sender, true, mockReceiver.target, futureDeadline, "0x1234"))
                .to.be.revertedWith("!deadline");
        });

        it("Should fail if called by non-owner", async function () {
            const nonce = 1;
            const sender = ethers.encodeBytes32String("sender");
            const deadline = (await ethers.provider.getBlock("latest"))!.timestamp + deadlineOffset;
            // await portal.sendMessage(sender, true, deadline, ["0x1234"]);
            await expect(portal.connect(otherAccount).receiveMessage(nonce, sender, true, mockReceiver.target, deadline, "0x1234"))
                .to.be.revertedWithCustomError(portal, "OwnableUnauthorizedAccount");
        });

        it("Should call receiveMessage on PortalMessageReceiverMock and emit MessageReceived event", async function () {
            const nonce = 1;
            const sender = ethers.encodeBytes32String("sender");
            const isPuzzleHash = true;
            const message = "0x1234";
            const deadline = (await ethers.provider.getBlock("latest"))!.timestamp + deadlineOffset;

            // await portal.sendMessage(sender, isPuzzleHash, deadline, [message]);

            await expect(portal.receiveMessage(nonce, sender, isPuzzleHash, mockReceiver.target, deadline, message))
                .to.not.be.reverted;
            await expect(mockReceiver.receiveMessage(nonce, sender, isPuzzleHash, message))
                .to.emit(mockReceiver, "MessageReceived")
                .withArgs(nonce, sender, isPuzzleHash, message);
        });
    });
});
