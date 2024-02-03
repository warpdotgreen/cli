import { expect } from "chai";
import { ethers } from "hardhat";
import { Portal, PortalMessageReceiverMock } from "../typechain-types";
import { BigNumberish } from "ethers";

describe("Portal", function () {
    let portal: Portal;
    let mockReceiver: PortalMessageReceiverMock;
    let owner: any,
        otherAccount: any,
        feeCollector: any,
        messageFee: BigNumberish;
    const nonce1 = ethers.encodeBytes32String("nonce1")

    beforeEach(async function () {
        [owner, otherAccount, feeCollector] = await ethers.getSigners();
        messageFee = ethers.parseEther("0.01");
        const PortalFactory = await ethers.getContractFactory("Portal");
        portal = await PortalFactory.deploy(owner.address, feeCollector.address, messageFee);
        const MockReceiverFactory = await ethers.getContractFactory("PortalMessageReceiverMock");
        mockReceiver = await MockReceiverFactory.deploy();
    });

    describe("Deployment", function () {
        it("Should set the right parameters", async function () {
            expect(await portal.owner()).to.equal(owner.address);
            expect(await portal.messageFee()).to.equal(messageFee);
            expect(await portal.feeCollector()).to.equal(feeCollector.address);
        });
    });

    describe("sendMessage", function () {
        it("should emit MessageSent and increment nonce", async function () {
            await expect(
                    portal.sendMessage(
                        "0x000001",
                        "0x01",
                        "0x0000000000000000000000000000000000000000000000000000000000000002",
                        ["0x1234"],
                        { value: messageFee }
                    ))
                .to.emit(portal, "MessageSent")
                .withArgs(
                    "0x0000000000000000000000000000000000000000000000000000000000000001",
                    "0x000001",
                    "0x01",
                    "0x0000000000000000000000000000000000000000000000000000000000000002",
                    ["0x1234"]
                );
            expect(await portal.ethNonce()).to.equal(1);
        });

        it("should fail if message fee is incorrect", async function () {
            await expect(portal.sendMessage("0x000001", "0x01", "0x0000000000000000000000000000000000000000000000000000000000000001", ["0x1234"]))
                .to.be.revertedWith("!fee");
        });
    });

    describe("receiveMessage", function () {
        let nonce: any;

        beforeEach(async function () {
            nonce = ethers.keccak256(ethers.toUtf8Bytes("nonce"));
        });

        it("should process valid message", async function () {
            await expect(
                await portal.receiveMessage(nonce, "0x000001", "0x01", "0x0000000000000000000000000000000000000000000000000000000000000001", mockReceiver.target, "0x1234")
            ).to.emit(mockReceiver, "MessageReceived").withArgs(
                nonce,
                "0x000001",
                "0x01",
                "0x0000000000000000000000000000000000000000000000000000000000000001",
                "0x1234"
            )
        });

        it("should fail is same nonce is used twice", async function () {
            await portal.receiveMessage(nonce, "0x000001", "0x01", "0x0000000000000000000000000000000000000000000000000000000000000001", mockReceiver.target, "0x1234");
            await expect(portal.receiveMessage(nonce, "0x000001", "0x01", "0x0000000000000000000000000000000000000000000000000000000000000001", mockReceiver.target, "0x1234"))
                .to.be.revertedWith("!nonce");
        });

        it("should fail if not called by owner", async function () {
            await expect(portal.connect(otherAccount).receiveMessage(nonce, "0x000001", "0x01", "0x0000000000000000000000000000000000000000000000000000000000000001", mockReceiver.target, "0x1234"))
                .to.be.revertedWithCustomError(portal, "OwnableUnauthorizedAccount");
        });
    });

    describe("withdrawFees", function () {
        it("should allow feeCollector to withdraw", async function () {
            const amount = ethers.parseEther("0.01");
            await portal.connect(otherAccount).sendMessage("0x000001", "0x01", "0x0000000000000000000000000000000000000000000000000000000000000001", ["0x1234"], { value: messageFee });
            await expect(portal.connect(feeCollector).withdrawFees([otherAccount.address], [amount]))
                .to.changeEtherBalances([portal, otherAccount], [-amount, amount]);
        });

        it("should allow withdrawal to multiple addresses", async function () {
            const amount1 = ethers.parseEther("0.003");
            const amount2 = ethers.parseEther("0.007");
            await portal.connect(otherAccount).sendMessage("0x000001", "0x01", "0x0000000000000000000000000000000000000000000000000000000000000001", ["0x1234"], { value: messageFee });
            await expect(portal.connect(feeCollector).withdrawFees(
                [otherAccount.address, feeCollector.address],
                [amount1, amount2])
            ).to.changeEtherBalances(
                [portal, otherAccount, feeCollector],
                [-messageFee, amount1, amount2]
            );
        });

        it("should fail if non-feeCollector tries to withdraw", async function () {
            const amount = ethers.parseEther("0.01");
            await expect(portal.withdrawFees([otherAccount.address], [amount]))
                .to.be.revertedWith("!feeCollector");
        });
    });
});
