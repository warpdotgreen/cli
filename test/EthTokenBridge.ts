import { expect } from "chai";
import { ethers } from "hardhat";
import { EthTokenBridge, ERC20Mock, Portal, WETHMock } from "../typechain-types";

describe("EthTokenBridge", function () {
    let ethTokenBridge: EthTokenBridge;
    let mockERC20: ERC20Mock;
    let mockWETH: WETHMock;
    let portal: Portal;
    let owner: any;
    let user: any;
    let anotherUser: any;
    let portalAddress: string;
    
    const messageFee = ethers.parseEther("0.001");
    const initialFee = 30n; // 0.3%
    const chiaToEthAmountFactor = 1000000000000000n;
    const abiCoder = new ethers.AbiCoder();
    const nonce1 = ethers.encodeBytes32String("nonce1");
    const chiaSideBurnPuzzle = ethers.encodeBytes32String("chia-burn-puzzle");
    const chiaSideMintPuzzle = ethers.encodeBytes32String("chia-mint-puzzle");
    const sourceChain = "0x786368";

    beforeEach(async function () {
        [owner, user, anotherUser] = await ethers.getSigners();

        const ERC20Factory = await ethers.getContractFactory("ERC20Mock");
        mockERC20 = await ERC20Factory.deploy("MockToken", "MTK");

        const WETHFactory = await ethers.getContractFactory("WETHMock");
        mockWETH = await WETHFactory.deploy();

        const PortalFactory = await ethers.getContractFactory("Portal");
        portal = await PortalFactory.deploy(owner.address, owner.address, messageFee);
        portalAddress = portal.target as string;

        const EthTokenBridgeFactory = await ethers.getContractFactory("EthTokenBridge");
        ethTokenBridge = await EthTokenBridgeFactory.deploy(portalAddress, mockWETH, chiaSideBurnPuzzle, chiaSideMintPuzzle);
    });

    describe("Deployment", function () {
        it("correct initial values", async function () {
            expect(await ethTokenBridge.portal()).to.equal(portalAddress);
            expect(await ethTokenBridge.chiaSideBurnPuzzle()).to.equal(chiaSideBurnPuzzle);
            expect(await ethTokenBridge.chiaSideMintPuzzle()).to.equal(chiaSideMintPuzzle);
            expect(await ethTokenBridge.fee()).to.equal(initialFee);
        });
    });

    describe("updateFee", function () {
        it("Should allow the owner to update the fee", async function () {
            const newFee = 200; // 2%
            await expect(ethTokenBridge.updateFee(newFee))
                .to.not.be.reverted;
            expect(await ethTokenBridge.fee()).to.equal(newFee);
        });

        it("Should revert if the new fee is too high", async function () {
            const newFee = 600; // 6%
            await expect(ethTokenBridge.updateFee(newFee))
                .to.be.revertedWith("fee too high");
        });

        it("Should revert if called by non-owner", async function () {
            const newFee = 200; // 2%
            await expect(ethTokenBridge.connect(user).updateFee(newFee))
                .to.be.revertedWithCustomError(ethTokenBridge, "OwnableUnauthorizedAccount");
        });
    });

    describe("bridgeToChia", function () {
        it("Should correctly bridge assets and deduct fees", async function () {
            const receiver = ethers.encodeBytes32String("receiverOnChia");
            const chiaAmount = ethers.parseUnits("10", 3);
            const expectedFee = chiaAmount * initialFee / 10000n;

            await mockERC20.mint(user.address, chiaAmount * chiaToEthAmountFactor);
            await mockERC20.connect(user).approve(ethTokenBridge.target, chiaAmount * chiaToEthAmountFactor);

            await expect(ethTokenBridge.connect(user).bridgeToChia(mockERC20.target, receiver, chiaAmount, { value: messageFee }))
                .to.emit(portal, "MessageSent");

            const newBalance = await mockERC20.balanceOf(ethTokenBridge.target);
            expect(newBalance).to.equal(chiaAmount * chiaToEthAmountFactor);

            const bridgeFeeAmount = await ethTokenBridge.fees(mockERC20.target);
            expect(bridgeFeeAmount).to.equal(expectedFee * chiaToEthAmountFactor);

        });

        it("Should fail if not enough balance", async function () {
            const receiver = ethers.encodeBytes32String("receiverOnChia");
            const amount = ethers.parseUnits("10", 18);

            await expect(ethTokenBridge.connect(user).bridgeToChia(mockERC20.target, receiver, amount, { value: messageFee }))
                .to.be.reverted;
        });

        it("Should fail if greater message fee is given", async function () {
            const receiver = ethers.encodeBytes32String("receiverOnChia");
            const amount = ethers.parseUnits("10", 18);

            await expect(ethTokenBridge.connect(user).bridgeToChia(mockERC20.target, receiver, amount, { value: messageFee * 2n}))
                .to.be.revertedWith("!fee");
        });

        it("Should fail if lower message fee is given", async function () {
            const receiver = ethers.encodeBytes32String("receiverOnChia");
            const amount = ethers.parseUnits("10", 18);

            await expect(ethTokenBridge.connect(user).bridgeToChia(mockERC20.target, receiver, amount, { value: messageFee / 2n}))
                .to.be.revertedWith("!fee");
        });
    });

    describe("receiveMessage", function () {
        it("Should correctly process received messages and transfer assets", async function () {
            const receiver = user.address;
            const amount = ethers.parseUnits("10", 3);
            const message = [
                ethers.zeroPadValue(mockERC20.target.toString(), 32),
                ethers.zeroPadValue(receiver, 32),
                ethers.zeroPadValue("0x" + amount.toString(16), 32)
            ]
            const expectedFee = amount * chiaToEthAmountFactor * initialFee / 10000n;

            await mockERC20.mint(ethTokenBridge.target, amount * chiaToEthAmountFactor);

            await portal.receiveMessage(nonce1, sourceChain, chiaSideBurnPuzzle, ethTokenBridge.target, message)
            
            const newBridgeBalance = await mockERC20.balanceOf(ethTokenBridge.target);
            expect(newBridgeBalance).to.equal(expectedFee);

            const bridgeFeeAmount = await ethTokenBridge.fees(mockERC20.target);
            expect(bridgeFeeAmount).to.equal(expectedFee);

            const newBalance = await mockERC20.balanceOf(receiver);
            expect(newBalance).to.equal(amount * chiaToEthAmountFactor - expectedFee);
        });

        it("Should fail if sender is not the portal", async function () {
            const message = [
                ethers.zeroPadValue(mockERC20.target.toString(), 32),
                ethers.zeroPadValue(user.address, 32),
                ethers.zeroPadValue("0x" + ethers.parseUnits("10", 3).toString(16), 32)
            ]

            await expect(ethTokenBridge.connect(user).receiveMessage(nonce1, sourceChain, chiaSideBurnPuzzle, message))
                .to.be.revertedWith("!portal");
        });

        it("Should fail if message source puzzle hash does not match", async function () {
            const invalidPuzzle = ethers.encodeBytes32String("invalidPuzzle");
            const message = [
                ethers.zeroPadValue(mockERC20.target.toString(), 32),
                ethers.zeroPadValue(user.address, 32),
                ethers.zeroPadValue("0x" + ethers.parseUnits("10", 3).toString(16), 32)
            ]

            await expect(portal.receiveMessage(nonce1, sourceChain, invalidPuzzle, ethTokenBridge.target, message))
                .to.be.revertedWith("!source");
        });
    });

    describe("withdrawFees", function () {
        const amount = ethers.parseUnits("10", 3);
        const expectedFee = amount * chiaToEthAmountFactor * initialFee / 10000n;

        beforeEach(async function () {
            const message = [
                ethers.zeroPadValue(mockERC20.target.toString(), 32),
                ethers.zeroPadValue(user.address, 32),
                ethers.zeroPadValue("0x" + amount.toString(16), 32)
            ]

            await mockERC20.mint(ethTokenBridge.target, amount * chiaToEthAmountFactor);

            await portal.receiveMessage(nonce1, sourceChain, chiaSideBurnPuzzle, ethTokenBridge.target, message)
            
            const newBridgeBalance = await mockERC20.balanceOf(ethTokenBridge.target);
            expect(newBridgeBalance).to.equal(expectedFee);

            const bridgeFeeAmount = await ethTokenBridge.fees(mockERC20.target);
            expect(bridgeFeeAmount).to.equal(expectedFee);
        });

        it("Should allow the owner to withdraw fees", async function () {
            const initialOwnerBalance = await mockERC20.balanceOf(owner.address);

            await ethTokenBridge.withdrawFees(mockERC20.target, [owner.address], [expectedFee]);

            const finalOwnerBalance = await mockERC20.balanceOf(owner.address);
            expect(finalOwnerBalance - initialOwnerBalance).to.equal(expectedFee);
        });

        it("Should fail if non-owner tries to withdraw fees", async function () {
            await expect(ethTokenBridge.connect(user).withdrawFees(mockERC20.target, [mockERC20.target], [expectedFee]))
                .to.be.revertedWithCustomError(ethTokenBridge, "OwnableUnauthorizedAccount");
        });
    });
});
