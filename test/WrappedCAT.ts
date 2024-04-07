import { expect } from "chai";
import { ethers } from "hardhat";
import { Portal, WrappedCAT } from "../typechain-types";
import { HardhatEthersSigner } from "@nomicfoundation/hardhat-ethers/signers";
import { getSig } from "./Portal";

describe.only("WrappedCAT", function () {
    let portal: Portal;
    let wrappedCAT: WrappedCAT;
    let owner: HardhatEthersSigner;
    let user: HardhatEthersSigner;
    let signer: HardhatEthersSigner;
    let otherChain = "0x786368";
    
    const messageFee = ethers.parseEther("0.001");
    const tip = 30n; // 0.3%
    const chiaToERC20AmountFactor = 10n ** 15n; // CATs have 3 decimals, 18 - 3 = 15
    const nonce1 = ethers.encodeBytes32String("nonce1");
    const lockerPuzzleHash = ethers.encodeBytes32String("locker-puzzle-hash");
    const unlockerPuzzleHash = ethers.encodeBytes32String("unlocker-puzzle-hash");

    beforeEach(async function () {
        [owner, user, signer] = await ethers.getSigners();

        const PortalFactory = await ethers.getContractFactory("Portal");
        portal = await PortalFactory.deploy();
        await portal.initialize(owner.address, messageFee, [ signer.address ], 1);

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
    });
});
