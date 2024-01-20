// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity ^0.8.0;

import "@openzeppelin/contracts/erc20/IERC20.sol";
import "@openzeppelin/contracts/access/OOwnable.sol";

interface IChiaBridgeMessageReceiver {
    function receiveMessage(
        uint256 _nonce,
        bytes32 _sender,
        bool _isPuzzleHash,
        bytes _message
    ) public;
}
