// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity ^0.8.20;

import "@openzeppelin/contracts/token/ERC20/extensions/ERC20Permit.sol";
import "@openzeppelin/contracts/token/ERC20/ERC20.sol";

contract WrappedCAT is ERC20, ERC20Permit, IPortalMessageReceiver {
    address public immutable portal;
    uint64 public immutable fee; // fee / 10000 %
    uint64 public immutable mojoToTokenRatio; // token amount on eth = mojos on Chia * mojoToTokenRatio
    bytes32 public immutable lockerPuzzleHash;
    bytes32 public immutable unlockerPuzzleHash;

    constructor(
        string memory _name,
        string memory _symbol,
        address _portal,
        uint64 _fee,
        uint64 _mojoToTokenRatio,
        bytes32 _lockerPuzzleHash,
        bytes32 _unlockerPuzzleHash
    ) Ownable(msg.sender) ERC20(_name, _symbol) ERC20Permit(_name) {
        portal = _portal;
        fee = _fee;
        mojoToTokenRatio = _mojoToTokenRatio;
        lockerPuzzleHash = _lockerPuzzleHash;
        unlockerPuzzleHash = _unlockerPuzzleHash;
    }

    function receiveMessage(
        bytes32 /* _nonce */,
        bytes3 _source_chain,
        bytes32 _source,
        bytes32[] memory _contents
    ) public {
        require(
            msg.sender == portal &&
                _source == lockerPuzzleHash &&
                _source_chain == bytes3("xch"),
            "!msg"
        );

        uint256 amount = uint256(_contents[1]) * mojoToTokenRatio;
        uint256 transferFee = (amount * fee) / 10000;

        _mint(address(uint160(uint256(_contents[0]))), amount - transferFee);
        _mint(portal, transferFee);
    }
}
