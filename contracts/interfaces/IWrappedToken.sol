// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity ^0.8.0;

import "@openzeppelin/contracts/erc20/IERC20.sol";
import "@openzeppelin/contracts/access/OOwnable.sol";

interface IWrappedToken is IERC20, IOwnable {
    function mint(address _to, uint256 _amount) public;

    function burn(address _from, uint256 _amount) public;
}
