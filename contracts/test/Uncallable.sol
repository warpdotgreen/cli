// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity 0.8.23;

import "../interfaces/IWETH.sol";

/* used as coinbase in some tests, and as caller to MilliETH in others */
/* simply rejects .call's from an address given in the constructor */

contract Uncallable {
    address public immutable blacklistedAddress;

    constructor(address _blacklistedAddress) {
        blacklistedAddress = _blacklistedAddress;
    }

    function call() external payable returns (bool) {
        return msg.sender != blacklistedAddress;
    }

    function withdrawMilliETH(uint256 _amount) external {
        IWETH(blacklistedAddress).withdraw(_amount);
    }
}
