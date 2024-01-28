// import { expect } from "chai";
// import { ethers } from "hardhat";
// import { EthTokenBridge, ERC20Mock, Portal } from "../typechain-types";

// describe("EthTokenBridge", function () {
//     let ethTokenBridge: EthTokenBridge;
//     let mockERC20: ERC20Mock;
//     let portal: Portal;
//     let owner: any;
//     let user: any;
//     let portalAddress: string;
//     let chiaSideBurnPuzzle: string;
//     let chiaSideMintPuzzle: string;
//     const initialFee = 100n; // 1%
//     const chiaToEthAmountFactor = 1000000000000000n;
//     const deadlineOffset = 3600; // 1 hour
//     const abiCoder = new ethers.AbiCoder()
//     const nonce1 = ethers.encodeBytes32String("nonce1")

//     beforeEach(async function () {
//         [owner, user] = await ethers.getSigners();

//         const ERC20Factory = await ethers.getContractFactory("ERC20Mock");
//         mockERC20 = await ERC20Factory.deploy("MockToken", "MTK");

//         const PortalFactory = await ethers.getContractFactory("Portal");
//         portal = await PortalFactory.deploy();
//         portalAddress = portal.target as string;

//         chiaSideBurnPuzzle = ethers.encodeBytes32String("burnPuzzle");
//         chiaSideMintPuzzle = ethers.encodeBytes32String("mintPuzzle");

//         const EthTokenBridgeFactory = await ethers.getContractFactory("EthTokenBridge");
//         ethTokenBridge = await EthTokenBridgeFactory.deploy(portalAddress, chiaSideBurnPuzzle, chiaSideMintPuzzle);
//     });

//     describe("Deployment", function () {
//         it("Should set the correct initial values", async function () {
//             expect(await ethTokenBridge.portal()).to.equal(portalAddress);
//             expect(await ethTokenBridge.chiaSideBurnPuzzle()).to.equal(chiaSideBurnPuzzle);
//             expect(await ethTokenBridge.chiaSideMintPuzzle()).to.equal(chiaSideMintPuzzle);
//             expect(await ethTokenBridge.fee()).to.equal(initialFee);
//         });
//     });

//     describe("updateFee", function () {
//         it("Should allow the owner to update the fee", async function () {
//             const newFee = 200; // 2%
//             await expect(ethTokenBridge.updateFee(newFee))
//                 .to.not.be.reverted;
//             expect(await ethTokenBridge.fee()).to.equal(newFee);
//         });

//         it("Should revert if the new fee is too high", async function () {
//             const newFee = 600; // 6%
//             await expect(ethTokenBridge.updateFee(newFee))
//                 .to.be.revertedWith("fee too high");
//         });

//         it("Should revert if called by non-owner", async function () {
//             const newFee = 200; // 2%
//             await expect(ethTokenBridge.connect(user).updateFee(newFee))
//                 .to.be.revertedWithCustomError(ethTokenBridge, "OwnableUnauthorizedAccount");
//         });
//     });

//     describe("bridgeToChia", function () {
//         it("Should correctly bridge assets and deduct fees", async function () {
//             const receiver = ethers.encodeBytes32String("receiverOnChia");
//             const chiaAmount = ethers.parseUnits("10", 3);
//             const expectedFee = chiaAmount * initialFee / 10000n;

//             await mockERC20.mint(user.address, chiaAmount * chiaToEthAmountFactor);
//             await mockERC20.connect(user).approve(ethTokenBridge.target, chiaAmount * chiaToEthAmountFactor);

//             await expect(ethTokenBridge.connect(user).bridgeToChia(mockERC20.target, receiver, chiaAmount))
//                 .to.emit(portal, "MessageSent");

//             const newBalance = await mockERC20.balanceOf(ethTokenBridge.target);
//             expect(newBalance).to.equal(chiaAmount * chiaToEthAmountFactor);

//             const bridgeFeeAmount = await ethTokenBridge.fees(mockERC20.target);
//             expect(bridgeFeeAmount).to.equal(expectedFee * chiaToEthAmountFactor);

//         });

//         it("Should fail if not enough balance", async function () {
//             const receiver = ethers.encodeBytes32String("receiverOnChia");
//             const amount = ethers.parseUnits("10", 18);

//             await expect(ethTokenBridge.connect(user).bridgeToChia(mockERC20.target, receiver, amount))
//                 .to.be.reverted;
//         });
//     });

//     describe("receiveMessage", function () {
//         it("Should correctly process received messages and transfer assets", async function () {
//             const receiver = user.address;
//             const amount = ethers.parseUnits("10", 3);
//             const message = abiCoder.encode(
//                 ["address", "address", "uint256"],
//                 [mockERC20.target, receiver, amount]
//             );
//             const expectedFee = amount * chiaToEthAmountFactor * initialFee / 10000n;

//             await mockERC20.mint(ethTokenBridge.target, amount * chiaToEthAmountFactor);

//             const deadline = (await ethers.provider.getBlock("latest"))!.timestamp + deadlineOffset;
//             await portal.receiveMessage(nonce1, chiaSideBurnPuzzle, true, ethTokenBridge.target, deadline, message)
            
//             const newBridgeBalance = await mockERC20.balanceOf(ethTokenBridge.target);
//             expect(newBridgeBalance).to.equal(expectedFee);

//             const bridgeFeeAmount = await ethTokenBridge.fees(mockERC20.target);
//             expect(bridgeFeeAmount).to.equal(expectedFee);

//             const newBalance = await mockERC20.balanceOf(receiver);
//             expect(newBalance).to.equal(amount * chiaToEthAmountFactor - expectedFee);
//         });

//         it("Should fail if sender is not the portal", async function () {
//             const message = abiCoder.encode(
//                 ["address", "address", "uint256"],
//                 [mockERC20.target, user.address, ethers.parseUnits("10", 3)]
//             );

//             await expect(ethTokenBridge.connect(user).receiveMessage(nonce1, chiaSideBurnPuzzle, true, message))
//                 .to.be.revertedWith("!portal");
//         });

//         it("Should fail if message sender puzzle hash does not match", async function () {
//             const invalidPuzzle = ethers.encodeBytes32String("invalidPuzzle");
//             const message = abiCoder.encode(
//                 ["address", "address", "uint256"],
//                 [mockERC20.target, user.address, ethers.parseUnits("10", 3)]
//             );

//             const deadline = (await ethers.provider.getBlock("latest"))!.timestamp + deadlineOffset;
//             await expect(portal.receiveMessage(nonce1, invalidPuzzle, true, ethTokenBridge.target, deadline, message))
//                 .to.be.revertedWith("!sender");
//         });
//     });

//     describe("withdrawFee", function () {
//         const amount = ethers.parseUnits("10", 3);
//         const expectedFee = amount * chiaToEthAmountFactor * initialFee / 10000n;

//         beforeEach(async function () {
//             const receiver = user.address;
//             const message = abiCoder.encode(
//                 ["address", "address", "uint256"],
//                 [mockERC20.target, receiver, amount]
//             );

//             await mockERC20.mint(ethTokenBridge.target, amount * chiaToEthAmountFactor);

//             const deadline = (await ethers.provider.getBlock("latest"))!.timestamp + deadlineOffset;
//             await portal.receiveMessage(nonce1, chiaSideBurnPuzzle, true, ethTokenBridge.target, deadline, message)
            
//             const newBridgeBalance = await mockERC20.balanceOf(ethTokenBridge.target);
//             expect(newBridgeBalance).to.equal(expectedFee);

//             const bridgeFeeAmount = await ethTokenBridge.fees(mockERC20.target);
//             expect(bridgeFeeAmount).to.equal(expectedFee);
//         });

//         it("Should allow the owner to withdraw fees", async function () {
//             const initialOwnerBalance = await mockERC20.balanceOf(owner.address);

//             await ethTokenBridge.withdrawFee(mockERC20.target);

//             const finalOwnerBalance = await mockERC20.balanceOf(owner.address);
//             expect(finalOwnerBalance - initialOwnerBalance).to.equal(expectedFee);
//         });

//         it("Should fail if non-owner tries to withdraw fees", async function () {
//             await expect(ethTokenBridge.connect(user).withdrawFee(mockERC20.target))
//                 .to.be.revertedWithCustomError(ethTokenBridge, "OwnableUnauthorizedAccount");
//         });
//     });
// });
