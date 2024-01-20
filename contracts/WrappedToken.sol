// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity ^0.8.0;

import "@openzeppelin/contracts/erc20/ERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

contract WrappedToken is ERC20, Ownable {
    constructor(
        string memory _name,
        string memory _symbol
    ) ERC20(_name, _symbol) {}

    function mint(address _to, uint256 _amount) public onlyOwner {
        _mint(_to, _amount);
    }

    function burn(address _from, uint256 _amount) public onlyOwner {
        _burn(_from, _amount);
    }
}
