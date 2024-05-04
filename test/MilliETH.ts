import { expect } from "chai";
import { ethers } from "hardhat";
import { MilliETH } from "../typechain-types";

describe.only("MilliETH", function () {
    let milliETH: MilliETH;
    let deployer: any;
    let user: any;

    beforeEach(async function () {
        [deployer, user] = await ethers.getSigners();
        const milliETHFactory = await ethers.getContractFactory("MilliETH", deployer);
        milliETH = await milliETHFactory.deploy();
    });

    describe("Deployment", function () {
        it("Should have correct name, symbol, and number of decimals", async function () {
            expect(await milliETH.name()).to.equal("milliETH");
            expect(await milliETH.symbol()).to.equal("milliETH");
            expect(await milliETH.decimals()).to.equal(3);
        });
    });

    describe("deposit", function () {
        it("Should allow deposits and mint tokens correctly", async function () {
            const depositValue = ethers.parseEther("3.133769"); // 1 ETH
            await expect(user.sendTransaction({
                to: milliETH.target,
                value: depositValue
            })).to.changeEtherBalance(user, -depositValue);

            // 1000 milliETH for 1 ETH
            expect(await milliETH.balanceOf(user.address)).to.equal(ethers.parseUnits("3133.769", 3));
        });

        it("Should allow minting 0.001 mETH", async function () {
            const depositValue = ethers.parseUnits("0.000001", "ether");
            await expect(user.sendTransaction({
                to: milliETH.target,
                value: depositValue
            })).to.changeEtherBalance(user, -depositValue);

            expect(await milliETH.balanceOf(user.address)).to.equal(ethers.parseUnits("0.001", 3));
        });

        it("Should reject deposits with low amounts", async function () {
            const depositValue = ethers.parseUnits("0.0000001", "ether");
            await expect(user.sendTransaction({
                to: milliETH.target,
                value: depositValue
            })).to.be.revertedWith("!msg.value");
        });

        it("Should reject deposits that leave change", async function () {
            const depositValue = ethers.parseUnits("10.0000001", "ether");
            await expect(user.sendTransaction({
                to: milliETH.target,
                value: depositValue
            })).to.be.revertedWith("!msg.value");
        });
    });

    describe("withdraw", function () {
        beforeEach(async function () {
            const depositValue = ethers.parseEther("1.337");
            await user.sendTransaction({
                to: milliETH.target,
                value: depositValue
            });

            expect(await milliETH.balanceOf(user.address)).to.equal(ethers.parseUnits("1337", 3));
        });

        it("Should allow withdrawals and burn tokens correctly", async function () {
            const withdrawAmount = ethers.parseUnits("1000", 3); // 1000 milliETH
            
            const initialBalance = await milliETH.balanceOf(user.address);
            await expect(() => milliETH.connect(user).withdraw(withdrawAmount))
                .to.changeEtherBalance(user, ethers.parseEther("1"));

            expect(await milliETH.balanceOf(user.address)).to.equal(initialBalance - withdrawAmount);
        });

        it("Should reject withdrawals with zero amount", async function () {
            await expect(milliETH.connect(user).withdraw(0)).to.be.revertedWith("!amount");
        });

        it("Should reject withdrawals exceeding balance", async function () {
            const excessiveAmount = ethers.parseUnits("2000", 3);
            await expect(
              milliETH.connect(user).withdraw(excessiveAmount)
            ).to.be.revertedWithCustomError(milliETH, "ERC20InsufficientBalance");
        });

        it("Should reject withdrawals when tokens cannot be returned", async function () {
            const withdrawAmount = ethers.parseUnits("1000", 3); // 1000 milliETH
            
            const UncallableFactory = await ethers.getContractFactory("Uncallable");
            const uncallable = await UncallableFactory.deploy(milliETH.target);
            
            await milliETH.connect(user).transfer(uncallable.target, withdrawAmount);
            
            await ethers.provider.send("hardhat_setCoinbase", [
                uncallable.target,
            ]);

            await expect(
                uncallable.withdrawMilliETH(withdrawAmount)
            ).to.be.revertedWith("!sent");

            // test cleanup
            // https://hardhat.org/hardhat-network/docs/reference#coinbase
            await ethers.provider.send("hardhat_setCoinbase", [
                "0xc014ba5ec014ba5ec014ba5ec014ba5ec014ba5e",
            ]);

        });
    });
});
