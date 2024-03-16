// SPDX-License-Identifier: MIT
/* yak tracks all over the place */
pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";

contract MilliETH is ERC20 {
    constructor() ERC20("milliETH", "mETH") {}

    function decimals() public pure returns (uint8) {
        return 3;
    }

    function deposit() public payable {
        // msg.value will have 18 decimals; we want 6 (1000 milliETH:1 ETH ratio + 3 decimals for 1 milliETH token)
        require(msg.value > 0 && msg.value % 10e12 == 0, "!msg.value");
        _mint(msg.sender, msg.value / 10e12);
    }

    function withdraw(uint256 amount) public {
        require(amount > 0, "!amount");
        _burn(msg.sender, amount);
        (bool sent, ) = msg.sender.call{value: amount * 10e12}("");
        require(sent, "!sent");
    }

    receive() external payable {
        deposit();
    }
}
