import { expect } from "chai";
import { ethers } from "hardhat";
import { EthTokenBridge, ERC20Mock, Portal, WETHMock, MilliETH } from "../typechain-types";
import { HardhatEthersSigner } from "@nomicfoundation/hardhat-ethers/signers";
import { getSig } from "./Portal";

const tokens = [
  { name: "WETHMock", decimals: 18n, wethToEthRatio: 1n,  type: "WETHMock" },
  { name: "MilliETH", decimals: 3n, wethToEthRatio: 10n ** 12n, type: "MilliETH" }
];

tokens.forEach(token => {
    describe.only(`EthTokenBridge (${token.name})`, function () {
        let ethTokenBridge: EthTokenBridge;
        let mockERC20: ERC20Mock;
        let weth: WETHMock | MilliETH;
        let portal: Portal;
        let owner: HardhatEthersSigner;
        let user: HardhatEthersSigner;
        let signer: HardhatEthersSigner;
        let portalAddress: string;
        let otherChain = "0x786368";
        
        const messageFee = ethers.parseEther("0.001");
        const tip = 30n; // 0.3%
        const chiaToERC20AmountFactor = 10n ** 15n; // CATs have 3 decimals, 18 - 3 = 15
        const nonce1 = ethers.encodeBytes32String("nonce1");
        const burnPuzzleHash = ethers.encodeBytes32String("burn-puzzle-hash");
        const mintPuzzleHash = ethers.encodeBytes32String("mint-puzzle-hash");

        beforeEach(async function () {
            [owner, user, signer] = await ethers.getSigners();

            const ERC20Factory = await ethers.getContractFactory("ERC20Mock");
            mockERC20 = await ERC20Factory.deploy("MockToken", "MTK");

            const WETHFactory = await ethers.getContractFactory(token.name);
            weth = (await WETHFactory.deploy()) as WETHMock | MilliETH;

            const PortalFactory = await ethers.getContractFactory("Portal");
            portal = await PortalFactory.deploy();
            await portal.initialize(owner.address, messageFee, [ signer.address ], 1);
            portalAddress = portal.target as string;

            const EthTokenBridgeFactory = await ethers.getContractFactory("EthTokenBridge");
            ethTokenBridge = await EthTokenBridgeFactory.deploy(
                tip, portalAddress, weth.target, token.wethToEthRatio, otherChain
            );

            await ethTokenBridge.initializePuzzleHashes(burnPuzzleHash, mintPuzzleHash)
        });

        describe("Deployment", function () {
            it("Should have correct initial values", async function () {
                expect(await ethTokenBridge.tip()).to.equal(tip);
                expect(await ethTokenBridge.portal()).to.equal(portal.target);
                expect(await ethTokenBridge.iweth()).to.equal(weth.target);
                expect(await ethTokenBridge.wethToEthRatio()).to.equal(token.wethToEthRatio);
                expect(await ethTokenBridge.otherChain()).to.equal(otherChain);
                expect(await ethTokenBridge.burnPuzzleHash()).to.equal(burnPuzzleHash);
                expect(await ethTokenBridge.mintPuzzleHash()).to.equal(mintPuzzleHash);
            });

            it("Should not allow setting puzzles a second time", async function () {
                await expect(ethTokenBridge.initializePuzzleHashes(burnPuzzleHash, mintPuzzleHash))
                    .to.be.revertedWith("nope");
            });
        });

        describe("bridgeToChia", function () {
            it("Should correctly bridge assets and deduct tip", async function () {
                const receiver = ethers.encodeBytes32String("receiverOnChia");
                const chiaAmount = ethers.parseUnits("10", 3);
                const expectedTip = chiaAmount * tip / 10000n;

                await mockERC20.mint(user.address, chiaAmount * chiaToERC20AmountFactor);
                await mockERC20.connect(user).approve(ethTokenBridge.target, chiaAmount * chiaToERC20AmountFactor);

                // https://hardhat.org/hardhat-runner/plugins/nomicfoundation-hardhat-chai-matchers#chaining-async-matchers
                const tx = ethTokenBridge.connect(user).bridgeToChia(mockERC20.target, receiver, chiaAmount, { value: messageFee });
                await expect(tx).to.changeTokenBalances(
                      mockERC20,
                      [user, ethTokenBridge.target, portal.target],
                      [-chiaAmount * chiaToERC20AmountFactor, (chiaAmount - expectedTip) * chiaToERC20AmountFactor, expectedTip * chiaToERC20AmountFactor]
                    )
                await expect(tx).to.emit(portal, "MessageSent");
            });

            it("Should fail if not enough balance", async function () {
                const receiver = ethers.encodeBytes32String("receiverOnChia");
                const amount = ethers.parseUnits("10", 18);

                await expect(
                  ethTokenBridge.connect(user).bridgeToChia(mockERC20.target, receiver, amount, { value: messageFee })
                ).to.be.reverted;
            });

            it("Should fail if greater message fee is given", async function () {
                const receiver = ethers.encodeBytes32String("receiverOnChia");
                const amount = ethers.parseUnits("10", 18);

                await expect(
                  ethTokenBridge.connect(user).bridgeToChia(mockERC20.target, receiver, amount, { value: messageFee * 2n})
                ).to.be.revertedWith("!fee");
            });

            it("Should fail if lower message fee is given", async function () {
                const receiver = ethers.encodeBytes32String("receiverOnChia");
                const amount = ethers.parseUnits("10", 18);

                await expect(
                  ethTokenBridge.connect(user).bridgeToChia(mockERC20.target, receiver, amount, { value: messageFee / 2n})
                ).to.be.revertedWith("!fee");
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
                const expectedTip = amount * chiaToERC20AmountFactor * tip / 10000n;

                await mockERC20.mint(ethTokenBridge.target, amount * chiaToERC20AmountFactor);

                const sig = await getSig(
                    nonce1, otherChain, burnPuzzleHash, ethTokenBridge.target.toString(), message,
                    [signer]
                );
                await expect(
                    portal.receiveMessage(
                      nonce1, otherChain, burnPuzzleHash, ethTokenBridge.target, message, sig
                  )
                ).to.changeTokenBalances(
                  mockERC20,
                  [ethTokenBridge, receiver, portal],
                  [-amount * chiaToERC20AmountFactor, amount * chiaToERC20AmountFactor - expectedTip, expectedTip]
                );
            });

            it("Should correctly process received messages and transfer ether", async function () {
                const receiver = user.address;
                const CATAmount = ethers.parseUnits("1.234", 3);
                const message = [
                    ethers.zeroPadValue(weth.target.toString(), 32),
                    ethers.zeroPadValue(receiver, 32),
                    ethers.zeroPadValue("0x" + CATAmount.toString(16).padStart(4, "0"), 32)
                ]
                const chiaToWETHFactor = token.type === "MilliETH" ? 1n : 10n ** 15n;

                const wethAmount = CATAmount * chiaToWETHFactor;
                const expectedTip = CATAmount * chiaToWETHFactor * tip / 10000n;

                await weth.deposit({ value: wethAmount * token.wethToEthRatio });
                await weth.transfer(ethTokenBridge.target, wethAmount);

                const sig = await getSig(
                    nonce1, otherChain, burnPuzzleHash, ethTokenBridge.target.toString(), message,
                    [signer]
                );
                await expect(
                    portal.receiveMessage(
                        nonce1, otherChain, burnPuzzleHash, ethTokenBridge.target, message, sig
                    )
                ).to.changeEtherBalances(
                    [weth, receiver, portal],
                    [
                        -wethAmount * token.wethToEthRatio,
                        (wethAmount - expectedTip) * token.wethToEthRatio,
                        expectedTip * token.wethToEthRatio
                    ]
                );
            });

            it("Should fail if sender is not the portal", async function () {
                const message = [
                    ethers.zeroPadValue(mockERC20.target.toString(), 32),
                    ethers.zeroPadValue(user.address, 32),
                    ethers.zeroPadValue("0x" + ethers.parseUnits("10", 3).toString(16), 32)
                ]

                await expect(ethTokenBridge.connect(user).receiveMessage(nonce1, otherChain, burnPuzzleHash, message))
                    .to.be.revertedWith("!msg");
            });

            it("Should fail if message source puzzle hash does not match", async function () {
                const invalidPuzzle = ethers.encodeBytes32String("invalidPuzzle");
                const message = [
                    ethers.zeroPadValue(mockERC20.target.toString(), 32),
                    ethers.zeroPadValue(user.address, 32),
                    ethers.zeroPadValue("0x" + ethers.parseUnits("10", 3).toString(16), 32)
                ]

                const sig = await getSig(
                    nonce1, otherChain, invalidPuzzle, ethTokenBridge.target.toString(), message,
                    [signer]
                );
                await expect(portal.receiveMessage(nonce1, otherChain, invalidPuzzle, ethTokenBridge.target, message, sig))
                    .to.be.revertedWith("!msg");
            });
        });

        describe("bridgeEtherToChia", function () {
            it("Should correctly bridge ETH and deduct fees", async function () {
                const receiver = ethers.encodeBytes32String("receiverOnChia");
                const ethToSend = ethers.parseEther("1");
                const expectedCATs = ethToSend / token.wethToEthRatio;
                let expectedTipInCAT = expectedCATs * tip / 10000n;

                const tx = ethTokenBridge.connect(user).bridgeEtherToChia(receiver, { value: ethToSend + messageFee });
                await expect(tx).to.changeTokenBalances(
                    weth,
                    [ethTokenBridge, portal],
                    [expectedCATs - expectedTipInCAT, expectedTipInCAT]
                );
                await expect(tx).to.emit(portal, "MessageSent");
            });

            it("Should fail if msg.value is too low", async function () {
                const receiver = ethers.encodeBytes32String("receiverOnChia");
                var ethToSend = messageFee * 2n / 3n;
                
                await expect(
                    ethTokenBridge.connect(user).bridgeEtherToChia(receiver, { value: ethToSend })
                ).to.be.reverted;
            });

            it("Should revert if no ETH is sent", async function () {
                const receiver = ethers.encodeBytes32String("receiverOnChia");

                await expect(
                    ethTokenBridge.connect(user).bridgeEtherToChia(receiver)
                ).to.be.reverted;
            });
        });

        // describe("bridgeToChiaWithPermit", function () {
        //     let deadline: number;
        //     let ownerSignature: any;
        //     let amount: bigint;
        //     let receiver: any;

        //     beforeEach(async function () {
        //         amount = ethers.parseUnits("100", 18);
        //         receiver = ethers.encodeBytes32String("receiverOnChia");
        //         deadline = (await ethers.provider.getBlock('latest'))!.timestamp + 86400;

        //         await mockERC20.mint(owner.address, ethers.parseUnits("1000", 18));

        //         const nonce = await mockERC20.nonces(owner.address);
        //         const domain = {
        //             name: await mockERC20.name(),
        //             version: '1',
        //             chainId: (await ethers.provider.getNetwork()).chainId,
        //             verifyingContract: mockERC20.target.toString()
        //         };
        //         const types = {
        //             Permit: [
        //                 { name: "owner", type: "address" },
        //                 { name: "spender", type: "address" },
        //                 { name: "value", type: "uint256" },
        //                 { name: "nonce", type: "uint256" },
        //                 { name: "deadline", type: "uint256" }
        //             ]
        //         }
        //         const message = {
        //             owner: owner.address,
        //             spender: ethTokenBridge.target,
        //             value: amount.toString(),
        //             nonce,
        //             deadline
        //         };

        //         ownerSignature = await owner.signTypedData(domain, types, message);
        //         ownerSignature = ethers.Signature.from(ownerSignature);
        //     });

        //     it("Should bridge tokens with permit and deduct fees", async function () {
        //         await expect(
        //             ethTokenBridge.connect(owner).bridgeToChiaWithPermit(
        //                 mockERC20.target,
        //                 receiver,
        //                 amount * 1000n / ethers.parseEther("1"),
        //                 deadline,
        //                 ownerSignature.v, ownerSignature.r, ownerSignature.s,
        //                 { value: messageFee }
        //             )
        //         ).to.emit(portal, "MessageSent");

        //         const bridgeBalance = await mockERC20.balanceOf(ethTokenBridge.target);
        //         expect(bridgeBalance).to.equal(amount);

        //         const feeAmount = amount * initialFee / 10000n;
        //         expect(await ethTokenBridge.fees(mockERC20.target)).to.equal(feeAmount);
        //     });

        //     it("should revert if permit already used", async function () {
        //         await ethTokenBridge.connect(owner).bridgeToChiaWithPermit(
        //             mockERC20.target,
        //             receiver,
        //             amount * 1000n / ethers.parseEther("1"),
        //             deadline,
        //             ownerSignature.v, ownerSignature.r, ownerSignature.s,
        //             { value: messageFee }
        //         );
            

        //         await expect(
        //             ethTokenBridge.connect(owner).bridgeToChiaWithPermit(
        //                 mockERC20.target,
        //                 receiver,
        //                 amount * 1000n / ethers.parseEther("1"),
        //                 deadline,
        //                 ownerSignature.v, ownerSignature.r, ownerSignature.s,
        //                 { value: messageFee }
        //             )
        //         ).to.be.reverted;
        //     });
        // });
    });
});
