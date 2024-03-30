// import { expect } from "chai";
// import { ethers } from "hardhat";
// import { EthTokenBridge, ERC20Mock, Portal, WETHMock, MilliETH } from "../typechain-types";
// import { HardhatEthersSigner } from "@nomicfoundation/hardhat-ethers/signers";
// import { getSig } from "./Portal";

// const tokens = [
//   { name: "WETHMock", decimals: 18n, wethToEthRatio: 1n,  type: "WETHMock" },
//   { name: "MilliETH", decimals: 3n, wethToEthRatio: 10n ** 12n, type: "MilliETH" }
// ];

// tokens.forEach(token => {
//     describe(`EthTokenBridge (${token.name})`, function () {
//         let ethTokenBridge: EthTokenBridge;
//         let mockERC20: ERC20Mock;
//         let weth: WETHMock | MilliETH;
//         let portal: Portal;
//         let owner: HardhatEthersSigner;
//         let user: HardhatEthersSigner;
//         let signer: HardhatEthersSigner;
//         let portalAddress: string;
        
//         const messageFee = ethers.parseEther("0.001");
//         const initialFee = 30n; // 0.3%
//         const chiaToERC20AmountFactor = 10n ** 15n; // CATs have 3 decimals, 18 - 3 = 15
//         const nonce1 = ethers.encodeBytes32String("nonce1");
//         const chiaSideBurnPuzzle = ethers.encodeBytes32String("chia-burn-puzzle");
//         const chiaSideMintPuzzle = ethers.encodeBytes32String("chia-mint-puzzle");
//         const sourceChain = "0x786368";

//         beforeEach(async function () {
//             [owner, user, signer] = await ethers.getSigners();

//             const ERC20Factory = await ethers.getContractFactory("ERC20Mock");
//             mockERC20 = await ERC20Factory.deploy("MockToken", "MTK");

//             const WETHFactory = await ethers.getContractFactory(token.name);
//             weth = (await WETHFactory.deploy()) as WETHMock | MilliETH;

//             const PortalFactory = await ethers.getContractFactory("Portal");
//             portal = await PortalFactory.deploy();
//             await portal.initialize(owner.address, messageFee, [ signer.address ], 1);
//             portalAddress = portal.target as string;

//             const EthTokenBridgeFactory = await ethers.getContractFactory("EthTokenBridge");
//             ethTokenBridge = await EthTokenBridgeFactory.deploy(
//                 portalAddress, owner.address, weth.target, token.wethToEthRatio
//             );

//             await ethTokenBridge.initializePuzzleHashes(chiaSideBurnPuzzle, chiaSideMintPuzzle)
//         });

//         describe("Deployment", function () {
//             it("Should have correct initial values", async function () {
//                 expect(await ethTokenBridge.portal()).to.equal(portalAddress);
//                 expect(await ethTokenBridge.chiaSideBurnPuzzle()).to.equal(chiaSideBurnPuzzle);
//                 expect(await ethTokenBridge.chiaSideMintPuzzle()).to.equal(chiaSideMintPuzzle);
//                 expect(await ethTokenBridge.fee()).to.equal(initialFee);
//             });

//             it("Should not allow setting puzzles a second time", async function () {
//                 await expect(ethTokenBridge.initializePuzzleHashes(chiaSideBurnPuzzle, chiaSideMintPuzzle))
//                     .to.be.revertedWith("nope");
//             });
//         });

//         describe("updateFee", function () {
//             it("Should allow the owner to update the fee", async function () {
//                 const newFee = 200; // 2%
//                 await expect(ethTokenBridge.updateFee(newFee))
//                     .to.not.be.reverted;
//                 expect(await ethTokenBridge.fee()).to.equal(newFee);
//             });

//             it("Should revert if the new fee is too high", async function () {
//                 const newFee = 1100; // 11%
//                 await expect(ethTokenBridge.updateFee(newFee))
//                     .to.be.revertedWith("fee too high");
//             });

//             it("Should revert if called by non-owner", async function () {
//                 const newFee = 200; // 2%
//                 await expect(ethTokenBridge.connect(user).updateFee(newFee))
//                     .to.be.revertedWithCustomError(ethTokenBridge, "OwnableUnauthorizedAccount");
//             });
//         });

//         describe("bridgeToChia", function () {
//             it("Should correctly bridge assets and deduct fees", async function () {
//                 const receiver = ethers.encodeBytes32String("receiverOnChia");
//                 const chiaAmount = ethers.parseUnits("10", 3);
//                 const expectedFee = chiaAmount * initialFee / 10000n;

//                 await mockERC20.mint(user.address, chiaAmount * chiaToERC20AmountFactor);
//                 await mockERC20.connect(user).approve(ethTokenBridge.target, chiaAmount * chiaToERC20AmountFactor);

//                 await expect(ethTokenBridge.connect(user).bridgeToChia(mockERC20.target, receiver, chiaAmount, { value: messageFee }))
//                     .to.emit(portal, "MessageSent");

//                 const newBalance = await mockERC20.balanceOf(ethTokenBridge.target);
//                 expect(newBalance).to.equal(chiaAmount * chiaToERC20AmountFactor);

//                 const bridgeFeeAmount = await ethTokenBridge.fees(mockERC20.target);
//                 expect(bridgeFeeAmount).to.equal(expectedFee * chiaToERC20AmountFactor);

//             });

//             it("Should fail if not enough balance", async function () {
//                 const receiver = ethers.encodeBytes32String("receiverOnChia");
//                 const amount = ethers.parseUnits("10", 18);

//                 await expect(ethTokenBridge.connect(user).bridgeToChia(mockERC20.target, receiver, amount, { value: messageFee }))
//                     .to.be.reverted;
//             });

//             it("Should fail if greater message fee is given", async function () {
//                 const receiver = ethers.encodeBytes32String("receiverOnChia");
//                 const amount = ethers.parseUnits("10", 18);

//                 await expect(ethTokenBridge.connect(user).bridgeToChia(mockERC20.target, receiver, amount, { value: messageFee * 2n}))
//                     .to.be.revertedWith("!fee");
//             });

//             it("Should fail if lower message fee is given", async function () {
//                 const receiver = ethers.encodeBytes32String("receiverOnChia");
//                 const amount = ethers.parseUnits("10", 18);

//                 await expect(ethTokenBridge.connect(user).bridgeToChia(mockERC20.target, receiver, amount, { value: messageFee / 2n}))
//                     .to.be.revertedWith("!fee");
//             });
//         });

//         describe("receiveMessage", function () {
//             it("Should correctly process received messages and transfer assets", async function () {
//                 const receiver = user.address;
//                 const amount = ethers.parseUnits("10", 3);
//                 const message = [
//                     ethers.zeroPadValue(mockERC20.target.toString(), 32),
//                     ethers.zeroPadValue(receiver, 32),
//                     ethers.zeroPadValue("0x" + amount.toString(16), 32)
//                 ]
//                 const expectedFee = amount * chiaToERC20AmountFactor * initialFee / 10000n;

//                 await mockERC20.mint(ethTokenBridge.target, amount * chiaToERC20AmountFactor);

//                 const sig = await getSig(
//                     nonce1, sourceChain, chiaSideBurnPuzzle, ethTokenBridge.target.toString(), message,
//                     [signer]
//                 );
//                 await portal.receiveMessage(
//                     nonce1, sourceChain, chiaSideBurnPuzzle, ethTokenBridge.target, message, sig
//                 )
                
//                 const newBridgeBalance = await mockERC20.balanceOf(ethTokenBridge.target);
//                 expect(newBridgeBalance).to.equal(expectedFee);

//                 const bridgeFeeAmount = await ethTokenBridge.fees(mockERC20.target);
//                 expect(bridgeFeeAmount).to.equal(expectedFee);

//                 const newBalance = await mockERC20.balanceOf(receiver);
//                 expect(newBalance).to.equal(amount * chiaToERC20AmountFactor - expectedFee);
//             });

//             it("Should correctly process received messages and transfer ether", async function () {
//                 const receiver = user.address;
//                 const CATAmount = ethers.parseUnits("1.234", 3);
//                 const message = [
//                     ethers.zeroPadValue(weth.target.toString(), 32),
//                     ethers.zeroPadValue(receiver, 32),
//                     ethers.zeroPadValue("0x" + CATAmount.toString(16).padStart(4, "0"), 32)
//                 ]
//                 const chiaToWETHFactor = token.type === "MilliETH" ? 1n : 10n ** 15n;

//                 const wethAmount = CATAmount * chiaToWETHFactor;
//                 const expectedFee = CATAmount * chiaToWETHFactor * initialFee / 10000n;

//                 await weth.deposit({ value: wethAmount * token.wethToEthRatio });
//                 await weth.transfer(ethTokenBridge.target, wethAmount);

//                 const sig = await getSig(
//                     nonce1, sourceChain, chiaSideBurnPuzzle, ethTokenBridge.target.toString(), message,
//                     [signer]
//                 );
//                 await expect(
//                     portal.receiveMessage(
//                         nonce1, sourceChain, chiaSideBurnPuzzle, ethTokenBridge.target, message, sig
//                     )
//                 ).to.changeEtherBalances(
//                     [weth, receiver],
//                     [
//                         -(wethAmount - expectedFee) * token.wethToEthRatio,
//                         (wethAmount - expectedFee) * token.wethToEthRatio
//                     ]
//                 )
                
//                 const newBridgeBalance = await weth.balanceOf(ethTokenBridge.target);
//                 expect(newBridgeBalance).to.equal(expectedFee);

//                 const bridgeFeeAmount = await ethTokenBridge.fees(weth.target);
//                 expect(bridgeFeeAmount).to.equal(expectedFee);
//             });

//             it("Should fail if sender is not the portal", async function () {
//                 const message = [
//                     ethers.zeroPadValue(mockERC20.target.toString(), 32),
//                     ethers.zeroPadValue(user.address, 32),
//                     ethers.zeroPadValue("0x" + ethers.parseUnits("10", 3).toString(16), 32)
//                 ]

//                 await expect(ethTokenBridge.connect(user).receiveMessage(nonce1, sourceChain, chiaSideBurnPuzzle, message))
//                     .to.be.revertedWith("!portal");
//             });

//             it("Should fail if message source puzzle hash does not match", async function () {
//                 const invalidPuzzle = ethers.encodeBytes32String("invalidPuzzle");
//                 const message = [
//                     ethers.zeroPadValue(mockERC20.target.toString(), 32),
//                     ethers.zeroPadValue(user.address, 32),
//                     ethers.zeroPadValue("0x" + ethers.parseUnits("10", 3).toString(16), 32)
//                 ]

//                 const sig = await getSig(
//                     nonce1, sourceChain, invalidPuzzle, ethTokenBridge.target.toString(), message,
//                     [signer]
//                 );
//                 await expect(portal.receiveMessage(nonce1, sourceChain, invalidPuzzle, ethTokenBridge.target, message, sig))
//                     .to.be.revertedWith("!source");
//             });
//         });

//         describe("withdrawFees", function () {
//             const amount = ethers.parseUnits("10", 3);
//             const expectedFee = amount * chiaToERC20AmountFactor * initialFee / 10000n;

//             beforeEach(async function () {
//                 const message = [
//                     ethers.zeroPadValue(mockERC20.target.toString(), 32),
//                     ethers.zeroPadValue(user.address, 32),
//                     ethers.zeroPadValue("0x" + amount.toString(16), 32)
//                 ]

//                 await mockERC20.mint(ethTokenBridge.target, amount * chiaToERC20AmountFactor);

//                 const sig = await getSig(
//                     nonce1, sourceChain, chiaSideBurnPuzzle, ethTokenBridge.target.toString(), message,
//                     [signer]
//                 );
//                 await portal.receiveMessage(nonce1, sourceChain, chiaSideBurnPuzzle, ethTokenBridge.target, message, sig)
                
//                 const newBridgeBalance = await mockERC20.balanceOf(ethTokenBridge.target);
//                 expect(newBridgeBalance).to.equal(expectedFee);

//                 const bridgeFeeAmount = await ethTokenBridge.fees(mockERC20.target);
//                 expect(bridgeFeeAmount).to.equal(expectedFee);
//             });

//             it("Should allow the owner to withdraw fees", async function () {
//                 const initialOwnerBalance = await mockERC20.balanceOf(owner.address);

//                 await ethTokenBridge.withdrawFees(mockERC20.target, [owner.address], [expectedFee]);

//                 const finalOwnerBalance = await mockERC20.balanceOf(owner.address);
//                 expect(finalOwnerBalance - initialOwnerBalance).to.equal(expectedFee);
//             });

//             it("Should fail if non-owner tries to withdraw fees", async function () {
//                 await expect(ethTokenBridge.connect(user).withdrawFees(mockERC20.target, [mockERC20.target], [expectedFee]))
//                     .to.be.revertedWithCustomError(ethTokenBridge, "OwnableUnauthorizedAccount");
//             });
//         });

//         describe("withdrawEtherFees", function () {
//             const amountWETHMojo = ethers.parseUnits("1337", 3);
//             const expectedFeeMojo = amountWETHMojo * initialFee / 10000n;

//             beforeEach(async function () {
//                 const message = [
//                     ethers.zeroPadValue(weth.target.toString(), 32),
//                     ethers.zeroPadValue(user.address, 32),
//                     ethers.zeroPadValue("0x" + amountWETHMojo.toString(16), 32)
//                 ]

//                 var etherValue = amountWETHMojo * 10n ** 15n;
//                 var amountWETH = etherValue;
//                 if (token.type === "MilliETH") {
//                     etherValue = amountWETHMojo * 10n ** 12n;
//                     amountWETH = amountWETHMojo;
//                 }
//                 await weth.deposit({ value: etherValue });
//                 await weth.transfer(ethTokenBridge.target, amountWETH);

//                 const sig = await getSig(
//                     nonce1, sourceChain, chiaSideBurnPuzzle, ethTokenBridge.target.toString(), message,
//                     [signer]
//                 );
//                 await portal.receiveMessage(nonce1, sourceChain, chiaSideBurnPuzzle, ethTokenBridge.target, message, sig)
                
//                 var expectedFee = expectedFeeMojo;
//                 if (token.type !== "MilliETH") {
//                     expectedFee = expectedFeeMojo * 10n ** 15n;
//                 }

//                 const newBridgeBalance = await weth.balanceOf(ethTokenBridge.target);
//                 expect(newBridgeBalance).to.equal(expectedFee);

//                 const bridgeFeeAmount = await ethTokenBridge.fees(weth.target);
//                 expect(bridgeFeeAmount).to.equal(expectedFee);
//             });

//             it("Should allow the owner to withdraw fees", async function () {
//                 var expectedFee = expectedFeeMojo;
//                 if (token.type !== "MilliETH") {
//                     expectedFee = expectedFeeMojo * 10n ** 15n;
//                 }

//                 await expect(
//                     ethTokenBridge.withdrawEtherFees([owner.address], [expectedFee])
//                 ).to.changeEtherBalances(
//                     [owner, weth], [expectedFee * token.wethToEthRatio, -expectedFee * token.wethToEthRatio]
//                 )

//                 expect(
//                     await ethTokenBridge.fees(weth.target)
//                 ).to.equal(0);
//             });

//             it("Should fail if non-owner tries to withdraw fees", async function () {
//                 await expect(ethTokenBridge.connect(user).withdrawEtherFees([owner.address], [expectedFeeMojo]))
//                     .to.be.revertedWithCustomError(ethTokenBridge, "OwnableUnauthorizedAccount");
//             });
//         });


//         describe("bridgeEtherToChia", function () {
//             it("Should correctly bridge ETH and deduct fees", async function () {
//                 const receiver = ethers.encodeBytes32String("receiverOnChia");
//                 const ethToSend = ethers.parseEther("1");
//                 const expectedCATs = ethToSend / token.wethToEthRatio;
//                 let expectedFeeInCAT = expectedCATs * initialFee / 10000n;

//                 await expect(
//                     ethTokenBridge.connect(user).bridgeEtherToChia(receiver, { value: ethToSend + messageFee })
//                 ).to.emit(portal, "MessageSent");

//                 expect(await weth.balanceOf(ethTokenBridge.target)).to.equal(expectedCATs);

//                 const bridgeFeeAmount = await ethTokenBridge.fees(weth.target);
//                 expect(bridgeFeeAmount).to.equal(expectedFeeInCAT);
//             });

//             it("Should fail if msg.value is too low", async function () {
//                 const receiver = ethers.encodeBytes32String("receiverOnChia");
//                 var ethToSend = messageFee * 2n / 3n;
                
//                 await expect(
//                     ethTokenBridge.connect(user).bridgeEtherToChia(receiver, { value: ethToSend })
//                 ).to.be.reverted;
//             });

//             it("Should revert if no ETH is sent", async function () {
//                 const receiver = ethers.encodeBytes32String("receiverOnChia");

//                 await expect(
//                     ethTokenBridge.connect(user).bridgeEtherToChia(receiver)
//                 ).to.be.reverted;
//             });
//         });

//         describe("rescueEther", function () {
//             const valueToRescue = ethers.parseEther("1");
//             beforeEach(async function () {
//                 await owner.sendTransaction({
//                     to: ethTokenBridge.target,
//                     value: valueToRescue
//                 });
//             });

//             it("Should allow owner to rescue Ether", async function () {
//                 const contractEthBalance = await ethers.provider.getBalance(ethTokenBridge.target);

//                 const feesBefore = await ethTokenBridge.fees(weth.target);
//                 expect(feesBefore).to.equal(0);

//                 await expect(ethTokenBridge.rescueEther(contractEthBalance / token.wethToEthRatio)).to.not.be.reverted;

//                 expect(await weth.balanceOf(ethTokenBridge.target)).to.equal(
//                     contractEthBalance / token.wethToEthRatio
//                 );

//                 const feesAfter = await ethTokenBridge.fees(weth.target);
//                 expect(feesAfter).to.equal(contractEthBalance / token.wethToEthRatio);
//             });

//             it("Should revert if non-owner tries to rescue Ether", async function () {
//                 await expect(ethTokenBridge.connect(user).rescueEther(valueToRescue))
//                     .to.be.revertedWithCustomError(ethTokenBridge, "OwnableUnauthorizedAccount");
//             });
//         });

//         describe("bridgeToChiaWithPermit", function () {
//             let deadline: number;
//             let ownerSignature: any;
//             let amount: bigint;
//             let receiver: any;

//             beforeEach(async function () {
//                 amount = ethers.parseUnits("100", 18);
//                 receiver = ethers.encodeBytes32String("receiverOnChia");
//                 deadline = (await ethers.provider.getBlock('latest'))!.timestamp + 86400;

//                 await mockERC20.mint(owner.address, ethers.parseUnits("1000", 18));

//                 const nonce = await mockERC20.nonces(owner.address);
//                 const domain = {
//                     name: await mockERC20.name(),
//                     version: '1',
//                     chainId: (await ethers.provider.getNetwork()).chainId,
//                     verifyingContract: mockERC20.target.toString()
//                 };
//                 const types = {
//                     Permit: [
//                         { name: "owner", type: "address" },
//                         { name: "spender", type: "address" },
//                         { name: "value", type: "uint256" },
//                         { name: "nonce", type: "uint256" },
//                         { name: "deadline", type: "uint256" }
//                     ]
//                 }
//                 const message = {
//                     owner: owner.address,
//                     spender: ethTokenBridge.target,
//                     value: amount.toString(),
//                     nonce,
//                     deadline
//                 };

//                 ownerSignature = await owner.signTypedData(domain, types, message);
//                 ownerSignature = ethers.Signature.from(ownerSignature);
//             });

//             it("Should bridge tokens with permit and deduct fees", async function () {
//                 await expect(
//                     ethTokenBridge.connect(owner).bridgeToChiaWithPermit(
//                         mockERC20.target,
//                         receiver,
//                         amount * 1000n / ethers.parseEther("1"),
//                         deadline,
//                         ownerSignature.v, ownerSignature.r, ownerSignature.s,
//                         { value: messageFee }
//                     )
//                 ).to.emit(portal, "MessageSent");

//                 const bridgeBalance = await mockERC20.balanceOf(ethTokenBridge.target);
//                 expect(bridgeBalance).to.equal(amount);

//                 const feeAmount = amount * initialFee / 10000n;
//                 expect(await ethTokenBridge.fees(mockERC20.target)).to.equal(feeAmount);
//             });

//             it("should revert if permit already used", async function () {
//                 await ethTokenBridge.connect(owner).bridgeToChiaWithPermit(
//                     mockERC20.target,
//                     receiver,
//                     amount * 1000n / ethers.parseEther("1"),
//                     deadline,
//                     ownerSignature.v, ownerSignature.r, ownerSignature.s,
//                     { value: messageFee }
//                 );
            

//                 await expect(
//                     ethTokenBridge.connect(owner).bridgeToChiaWithPermit(
//                         mockERC20.target,
//                         receiver,
//                         amount * 1000n / ethers.parseEther("1"),
//                         deadline,
//                         ownerSignature.v, ownerSignature.r, ownerSignature.s,
//                         { value: messageFee }
//                     )
//                 ).to.be.reverted;
//             });
//         });
//     });
// });
