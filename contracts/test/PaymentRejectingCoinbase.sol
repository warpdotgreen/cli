// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity ^0.8.20;

/* used as coinbase in some tests */
/* simply rejects payments from an address given in the constructor */

contract PaymentRejectingCoinbase {
    address public immutable blacklistedAddress;

    constructor(address _blacklistedAddress) {
        blacklistedAddress = _blacklistedAddress;
    }

    function call() external payable returns (bool) {
        return msg.sender != blacklistedAddress;
    }
}
