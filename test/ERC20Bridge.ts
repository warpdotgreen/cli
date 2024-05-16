import { expect } from "chai";
import { ethers } from "hardhat";
import { ERC20Mock, Portal, WETHMock, MilliETH, ERC20Bridge } from "../typechain-types";
import { HardhatEthersSigner } from "@nomicfoundation/hardhat-ethers/signers";
import { getSig } from "./Portal";

const wethTokens = [
  { name: "WETHMock", decimals: 18n, wethToEthRatio: 1n,  type: "WETHMock" },
  { name: "MilliETH", decimals: 3n, wethToEthRatio: 10n ** 12n, type: "MilliETH" }
];

const tokens = [
  { decimals: 18 },
  { decimals: 12 },
  { decimals: 4 },
  { decimals: 3 },
]

wethTokens.forEach(wethToken => {
  tokens.forEach(token => {
    describe(`ERC20Bridge (WETH=${wethToken.name}; mock ERC20 decimals=${token.decimals})`, function () {
        let erc20Bridge: ERC20Bridge;
        let mockERC20: ERC20Mock;
        let weth: WETHMock | MilliETH;
        let portal: Portal;
        let owner: HardhatEthersSigner;
        let user: HardhatEthersSigner;
        let signer: HardhatEthersSigner;
        let portalAddress: string;
        let otherChain = "0x786368";
        
        const messageToll = ethers.parseEther("0.001");
        const tip = 30n; // 0.3%
        const chiaToERC20AmountFactor = 10n ** BigInt(token.decimals - 3); // CATs have 3 decimals, 18 - 3 = 15
        const nonce1 = ethers.encodeBytes32String("nonce1");
        const burnPuzzleHash = ethers.encodeBytes32String("burn-puzzle-hash");
        const mintPuzzleHash = ethers.encodeBytes32String("mint-puzzle-hash");

        beforeEach(async function () {
            [owner, user, signer] = await ethers.getSigners();

            const ERC20Factory = await ethers.getContractFactory("ERC20Mock");
            mockERC20 = await ERC20Factory.deploy("MockToken", "MTK", token.decimals);

            const WETHFactory = await ethers.getContractFactory(wethToken.name);
            weth = (await WETHFactory.deploy()) as WETHMock | MilliETH;

            const PortalFactory = await ethers.getContractFactory("Portal");
            portal = await PortalFactory.deploy();
            await portal.initialize(owner.address, messageToll, [ signer.address ], 1, [ otherChain ]);
            portalAddress = portal.target as string;

            const ERC20BridgeFactory = await ethers.getContractFactory("ERC20Bridge");
            erc20Bridge = await ERC20BridgeFactory.deploy(
                tip, portalAddress, weth.target, wethToken.wethToEthRatio, otherChain
            );

            await erc20Bridge.initializePuzzleHashes(burnPuzzleHash, mintPuzzleHash)
        });

        describe("Deployment", function () {
            it("Should have correct initial values", async function () {
                expect(await erc20Bridge.tip()).to.equal(tip);
                expect(await erc20Bridge.portal()).to.equal(portal.target);
                expect(await erc20Bridge.iweth()).to.equal(weth.target);
                expect(await erc20Bridge.wethToEthRatio()).to.equal(wethToken.wethToEthRatio);
                expect(await erc20Bridge.otherChain()).to.equal(otherChain);
                expect(await erc20Bridge.burnPuzzleHash()).to.equal(burnPuzzleHash);
                expect(await erc20Bridge.mintPuzzleHash()).to.equal(mintPuzzleHash);
            });

            it("Should not allow setting puzzles a second time", async function () {
                await expect(erc20Bridge.initializePuzzleHashes(burnPuzzleHash, mintPuzzleHash))
                    .to.be.revertedWith("nope");
            });

            it("Should not allow a tip that is too high", async function () {
                const ERC20BridgeFactory = await ethers.getContractFactory("ERC20Bridge");
                const invalidTip = 10001n; // 100.01%

                await expect(
                    ERC20BridgeFactory.deploy(
                        invalidTip, portalAddress, weth.target, wethToken.wethToEthRatio, otherChain
                    )
                ).to.be.revertedWith("!tip");
            });

            it("Should not allow a tip that is too low", async function () {
                const ERC20BridgeFactory = await ethers.getContractFactory("ERC20Bridge");
                const invalidTip = 0; // 0%

                await expect(
                    ERC20BridgeFactory.deploy(
                        invalidTip, portalAddress, weth.target, wethToken.wethToEthRatio, otherChain
                    )
                ).to.be.revertedWith("!tip");
            });

            it("Should not allow portal address to be address(0)", async function () {
                const ERC20BridgeFactory = await ethers.getContractFactory("ERC20Bridge");

                await expect(
                    ERC20BridgeFactory.deploy(
                        tip, ethers.ZeroAddress, weth.target, wethToken.wethToEthRatio, otherChain
                    )
                ).to.be.revertedWith("!addrs");
            });

            it("Should not allow WETH address to be address(0)", async function () {
                const ERC20BridgeFactory = await ethers.getContractFactory("ERC20Bridge");

                await expect(
                    ERC20BridgeFactory.deploy(
                        tip, portalAddress, ethers.ZeroAddress, wethToken.wethToEthRatio, otherChain
                    )
                ).to.be.revertedWith("!addrs");
            });
        });

        describe("bridgeToChia", function () {
            it("Should correctly bridge assets and deduct tip", async function () {
                const receiver = ethers.encodeBytes32String("receiverOnChia");
                const chiaAmount = ethers.parseUnits("10", 3);
                const expectedTip = chiaAmount * tip / 10000n;

                await mockERC20.mint(user.address, chiaAmount * chiaToERC20AmountFactor);
                await mockERC20.connect(user).approve(erc20Bridge.target, chiaAmount * chiaToERC20AmountFactor);

                // https://hardhat.org/hardhat-runner/plugins/nomicfoundation-hardhat-chai-matchers#chaining-async-matchers
                const tx = erc20Bridge.connect(user).bridgeToChia(mockERC20.target, receiver, chiaAmount, { value: messageToll });
                await expect(tx).to.changeTokenBalances(
                      mockERC20,
                      [user, erc20Bridge.target, portal.target],
                      [-chiaAmount * chiaToERC20AmountFactor, (chiaAmount - expectedTip) * chiaToERC20AmountFactor, expectedTip * chiaToERC20AmountFactor]
                    )

                await expect(tx).to.emit(portal, "MessageSent").withArgs(
                    "0x0000000000000000000000000000000000000000000000000000000000000001",
                    erc20Bridge.target,
                    otherChain,
                    mintPuzzleHash,
                    [
                        ethers.zeroPadValue(mockERC20.target.toString(), 32),
                        ethers.zeroPadValue(receiver, 32),
                        ethers.zeroPadValue("0x" + (chiaAmount - expectedTip).toString(16), 32)
                    ]
                );
            });

            it("Should fail if not enough balance", async function () {
                const receiver = ethers.encodeBytes32String("receiverOnChia");
                const amount = ethers.parseUnits("10", token.decimals);

                await expect(
                  erc20Bridge.connect(user).bridgeToChia(mockERC20.target, receiver, amount, { value: messageToll })
                ).to.be.reverted;
            });

            it("Should fail if greater message toll is given", async function () {
                const receiver = ethers.encodeBytes32String("receiverOnChia");
                const amount = ethers.parseUnits("10", token.decimals);

                await expect(
                  erc20Bridge.connect(user).bridgeToChia(mockERC20.target, receiver, amount, { value: messageToll * 2n})
                ).to.be.revertedWith("!toll");
            });

            it("Should fail if lower message toll is given", async function () {
                const receiver = ethers.encodeBytes32String("receiverOnChia");
                const amount = ethers.parseUnits("10", token.decimals);

                await expect(
                  erc20Bridge.connect(user).bridgeToChia(mockERC20.target, receiver, amount, { value: messageToll / 2n})
                ).to.be.revertedWith("!toll");
            });

            it("Should fail if no message toll is given", async function () {
                const receiver = ethers.encodeBytes32String("receiverOnChia");
                const amount = ethers.parseUnits("10", token.decimals);

                await expect(
                  erc20Bridge.connect(user).bridgeToChia(mockERC20.target, receiver, amount)
                ).to.be.revertedWith("!toll");
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

                await mockERC20.mint(erc20Bridge.target, amount * chiaToERC20AmountFactor);

                const sig = await getSig(
                    nonce1, otherChain, burnPuzzleHash, erc20Bridge.target.toString(), message,
                    [signer]
                );
                await expect(
                    portal.receiveMessage(
                      nonce1, otherChain, burnPuzzleHash, erc20Bridge.target, message, sig
                  )
                ).to.changeTokenBalances(
                  mockERC20,
                  [erc20Bridge, receiver, portal],
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
                const chiaToWETHFactor = wethToken.type === "MilliETH" ? 1n : 10n ** 15n;

                const wethAmount = CATAmount * chiaToWETHFactor;
                const expectedTip = CATAmount * chiaToWETHFactor * tip / 10000n;

                await weth.deposit({ value: wethAmount * wethToken.wethToEthRatio });
                await weth.transfer(erc20Bridge.target, wethAmount);

                const sig = await getSig(
                    nonce1, otherChain, burnPuzzleHash, erc20Bridge.target.toString(), message,
                    [signer]
                );
                await expect(
                    portal.receiveMessage(
                        nonce1, otherChain, burnPuzzleHash, erc20Bridge.target, message, sig
                    )
                ).to.changeEtherBalances(
                    [weth, receiver, portal],
                    [
                        -wethAmount * wethToken.wethToEthRatio,
                        (wethAmount - expectedTip) * wethToken.wethToEthRatio,
                        expectedTip * wethToken.wethToEthRatio
                    ]
                );
            });

            it("Should fail if sender is not the portal", async function () {
                const message = [
                    ethers.zeroPadValue(mockERC20.target.toString(), 32),
                    ethers.zeroPadValue(user.address, 32),
                    ethers.zeroPadValue("0x" + ethers.parseUnits("10", 3).toString(16), 32)
                ]

                await expect(erc20Bridge.connect(user).receiveMessage(nonce1, otherChain, burnPuzzleHash, message))
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
                    nonce1, otherChain, invalidPuzzle, erc20Bridge.target.toString(), message,
                    [signer]
                );
                await expect(portal.receiveMessage(nonce1, otherChain, invalidPuzzle, erc20Bridge.target, message, sig))
                    .to.be.revertedWith("!msg");
            });
        });

        describe("bridgeEtherToChia", function () {
            it("Should correctly bridge ETH and deduct tips", async function () {
                const receiver = ethers.encodeBytes32String("receiverOnChia");
                const ethToSend = ethers.parseEther("1");
                const expectedCATs = ethToSend / wethToken.wethToEthRatio;
                let expectedTipInCAT = expectedCATs * tip / 10000n;

                const tx = erc20Bridge.connect(user).bridgeEtherToChia(receiver, messageToll, { value: ethToSend + messageToll });
                await expect(tx).to.changeTokenBalances(
                    weth,
                    [erc20Bridge, portal],
                    [expectedCATs - expectedTipInCAT, expectedTipInCAT]
                );
                await expect(tx).to.emit(portal, "MessageSent");
            });

            if(wethToken.type === "MilliETH") {
                it("Should fail if there is leftover ether", async function () {
                    const receiver = ethers.encodeBytes32String("receiverOnChia");
                    const ethToSend = ethers.parseEther("1");

                    await expect(
                        erc20Bridge.connect(user).bridgeEtherToChia(receiver, messageToll, { value: ethToSend + messageToll + 1n })
                    ).to.be.revertedWith("!amnt");
                });

                it("Should fail if ether amount after deducting message toll is too low", async function () {
                    const receiver = ethers.encodeBytes32String("receiverOnChia");

                    await expect(
                        erc20Bridge.connect(user).bridgeEtherToChia(receiver, messageToll, { value: messageToll + 1n })
                    ).to.be.revertedWith("!amnt");
                });
            }

            it("Should fail if msg.value is too low", async function () {
                const receiver = ethers.encodeBytes32String("receiverOnChia");
                var ethToSend = messageToll * 2n / 3n;
                
                await expect(
                    erc20Bridge.connect(user).bridgeEtherToChia(receiver, messageToll, { value: ethToSend })
                ).to.be.reverted;
            });

            it("Should revert if no ETH is sent", async function () {
                const receiver = ethers.encodeBytes32String("receiverOnChia");

                await expect(
                    erc20Bridge.connect(user).bridgeEtherToChia(receiver, messageToll)
                ).to.be.reverted;
            });

            it("Should revert if maxMessageToll is too low", async function () {
                const receiver = ethers.encodeBytes32String("receiverOnChia");
                const ethToSend = ethers.parseEther("1");

                await expect(
                    erc20Bridge.connect(user).bridgeEtherToChia(receiver, messageToll - 1n, { value: ethToSend + messageToll })
                ).to.be.revertedWith("!toll");
            });
        });

        describe("bridgeToChiaWithPermit", function () {
            let deadline: number;
            let ownerSignature: any;
            let amount: bigint;
            let receiver: any;

            beforeEach(async function () {
                amount = ethers.parseUnits("100", token.decimals);
                receiver = ethers.encodeBytes32String("receiverOnChia");
                deadline = (await ethers.provider.getBlock('latest'))!.timestamp + 86400;

                await mockERC20.mint(owner.address, ethers.parseUnits("1000", token.decimals));

                const nonce = await mockERC20.nonces(owner.address);
                const domain = {
                    name: await mockERC20.name(),
                    version: '1',
                    chainId: (await ethers.provider.getNetwork()).chainId,
                    verifyingContract: mockERC20.target.toString()
                };
                const types = {
                    Permit: [
                        { name: "owner", type: "address" },
                        { name: "spender", type: "address" },
                        { name: "value", type: "uint256" },
                        { name: "nonce", type: "uint256" },
                        { name: "deadline", type: "uint256" }
                    ]
                }
                const message = {
                    owner: owner.address,
                    spender: erc20Bridge.target,
                    value: amount.toString(),
                    nonce,
                    deadline
                };

                ownerSignature = await owner.signTypedData(domain, types, message);
                ownerSignature = ethers.Signature.from(ownerSignature);
            });

            it("Should bridge tokens with permit and deduct tips", async function () {
                const tx = erc20Bridge.connect(owner).bridgeToChiaWithPermit(
                    mockERC20.target,
                    receiver,
                    amount / chiaToERC20AmountFactor,
                    deadline,
                    ownerSignature.v, ownerSignature.r, ownerSignature.s,
                    { value: messageToll }
                );

                const tipAmount = amount * tip / 10000n;
                await expect(tx).to.changeTokenBalances(
                  mockERC20,
                  [owner, erc20Bridge, portal],
                  [-amount, amount - tipAmount, tipAmount]
                );
                await expect(tx).to.emit(portal, "MessageSent");
            });

            it("should revert if permit already used", async function () {
                await erc20Bridge.connect(owner).bridgeToChiaWithPermit(
                    mockERC20.target,
                    receiver,
                    amount / chiaToERC20AmountFactor,
                    deadline,
                    ownerSignature.v, ownerSignature.r, ownerSignature.s,
                    { value: messageToll }
                );
            

                await expect(
                    erc20Bridge.connect(owner).bridgeToChiaWithPermit(
                        mockERC20.target,
                        receiver,
                        amount / chiaToERC20AmountFactor,
                        deadline,
                        ownerSignature.v, ownerSignature.r, ownerSignature.s,
                        { value: messageToll }
                    )
                ).to.be.reverted;
            });

            it("Should fail if greater message toll is given", async function () {
                const receiver = ethers.encodeBytes32String("receiverOnChia");
                const amount = ethers.parseUnits("10", token.decimals);

                await expect(
                    erc20Bridge.connect(owner).bridgeToChiaWithPermit(
                        mockERC20.target,
                        receiver,
                        amount / chiaToERC20AmountFactor,
                        deadline,
                        ownerSignature.v, ownerSignature.r, ownerSignature.s,
                        { value: messageToll * 2n }
                    )
                ).to.be.revertedWith("!toll");
            });

            it("Should fail if lower message toll is given", async function () {
                const receiver = ethers.encodeBytes32String("receiverOnChia");
                const amount = ethers.parseUnits("10", token.decimals);

                await expect(
                  erc20Bridge.connect(owner).bridgeToChiaWithPermit(
                        mockERC20.target,
                        receiver,
                        amount / chiaToERC20AmountFactor,
                        deadline,
                        ownerSignature.v, ownerSignature.r, ownerSignature.s,
                        { value: messageToll / 2n }
                    )
                ).to.be.revertedWith("!toll");
            });

            it("Should fail if no message toll is given", async function () {
                const receiver = ethers.encodeBytes32String("receiverOnChia");
                const amount = ethers.parseUnits("10", token.decimals);

                await expect(
                  erc20Bridge.connect(owner).bridgeToChiaWithPermit(
                        mockERC20.target,
                        receiver,
                        amount / chiaToERC20AmountFactor,
                        deadline,
                        ownerSignature.v, ownerSignature.r, ownerSignature.s
                    )
                ).to.be.revertedWith("!toll");
            });
        });

        describe("receive", function () {
            it("Should revert if someone tries to incorrectly send ether", async function () {
                await expect(
                    user.sendTransaction({ to: erc20Bridge.target, value: ethers.parseEther("1") })
                ).to.be.revertedWith("!sender");
            });
        });
    });
  });
});
