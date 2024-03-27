// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/extensions/ERC20Permit.sol";
import "@openzeppelin/contracts/token/ERC20/extensions/ERC20Permit.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

contract WrappedCAT is Ownable, ERC20, ERC20Permit {
    constructor(
        string memory _name,
        string memory _symbol
    ) Ownable(msg.sender) ERC20(_name, _symbol) ERC20Permit(_name) {}
}
