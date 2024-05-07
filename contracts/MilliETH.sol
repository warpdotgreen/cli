// SPDX-License-Identifier: MIT
/* yak tracks all over the place */
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "./interfaces/IWETH.sol";

/**
 * @title   milliETH Token Contract
 * @notice  milliETH is an ERC-20token, where 1 milliETH is equivalent to 1/1000th of one ether.
 * @dev     No fees or tips. The token only has 3 decimals.
 */
contract MilliETH is ERC20, IWETH {
    /**
     * @notice  MilliETH constructor
     * @dev     We opted against using "mETH" as the symbol.
     */
    constructor() ERC20("milliETH", "milliETH") {}

    /**
     * @notice  Returns milliETH's number of decimals, which is 3.
     * @dev     Overrides the decimals method to set milliETH decimals to 3.
     * @return  The number of decimals for milliETH tokens.
     */
    function decimals() public pure override returns (uint8) {
        return 3;
    }

    /**
     * @notice  Used to mint milliETH equivalent to the deposited ether value.
     * @dev     Accepts ETH, mints corresponding milliETH tokens considering the conversion rate of 1000 milliETH per ETH. Requires the deposited ETH to be divisible by 1e12 wei for correct conversion.
     */
    function deposit() public payable {
        // msg.value will have 18 decimals; we want 6 (1000 milliETH:1 ETH ratio + 3 decimals for 1 milliETH token)
        require(msg.value > 0 && msg.value % 1e12 == 0, "!msg.value");
        _mint(msg.sender, msg.value / 1e12);
    }

    /**
     * @notice  Called to withdraw milliETH. Equivalent ether value is sent back.
     * @dev     Burns the specified amount of milliETH and sends the equivalent amount of ETH back to the sender.
     * @param   amount The amount of milliETH to burn and convert to ether.
     */
    function withdraw(uint256 amount) external {
        require(amount > 0, "!amount");
        _burn(msg.sender, amount);
        (bool sent, ) = msg.sender.call{value: amount * 1e12}("");
        require(sent, "!sent");
    }

    /**
     * @notice  Receives ether and automatically deposits it, minting milliETH tokens to the sender.
     * @dev     Allows the contract to accept direct ETH transfers and handles them by calling the deposit function.
     */
    receive() external payable {
        deposit();
    }
}
