// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

import "@openzeppelin/contracts/token/ERC20/extensions/ERC20Permit.sol";
import "@openzeppelin/contracts/token/ERC20/extensions/ERC20Permit.sol";
import "@openzeppelin/contracts/access/Ownable.sol";

contract WrappedCATFactory is Ownable, ERC20, ERC20Permit {
    constructor(
        string memory _name,
        string memory _symbol
    ) Ownable(msg.sender) ERC20(_name, _symbol) ERC20Permit(_name) {}

    function mint(address _to, uint256 _amount) public onlyOwner {
        _mint(_to, _amount);
    }

    function burn(address _from, uint256 _amount) public onlyOwner {
        _burn(_from, _amount);
    }
}
