// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "./WrappedCAT.sol";

contract WrappedCATFactory is Ownable, IPortalMessageReceiver {
    struct WrappedCATInfo {
        address deploymentAddress;
        uint64 mojoToTokenRatio;
    }
    mapping(bytes32 => WrappedCATInfo) public wrappedCATInfos;

    mapping(address => uint256) public fees;
    uint256 public fee = 100; // initial fee - 1%
    address public immutable portal;

    bytes32 public chiaSideLockerPuzzle;
    bytes32 public chiaSideUnlockerPuzzle;

    constructor(address _portal, address _feeManager) Ownable(_feeManager) {
        portal = _portal;

        WrappedCAT wXCH = new WrappedCAT("Wrapped Chia", "wXCH");
        WrappedCATInfo memory wXCHInfo = WrappedCATInfo({
            deploymentAddress: address(wXCH),
            mojoToTokenRatio: 1e6
        });

        wrappedCATInfos[bytes32(0)] = wXCHInfo;
    }

    function initializePuzzleHashes(
        bytes32 _chiaSideLockerPuzzle,
        bytes32 _chiaSideUnlockerPuzzle
    ) public onlyOwner {
        require(
            chiaSideLockerPuzzle == bytes32(0) &&
                chiaSideUnlockerPuzzle == bytes32(0),
            "nope"
        );
        chiaSideLockerPuzzle = _chiaSideLockerPuzzle;
        chiaSideUnlockerPuzzle = _chiaSideUnlockerPuzzle;
    }

    // fee between 0% and 10%
    function updateFee(uint256 _newFee) public onlyOwner {
        require(_newFee <= 1000, "fee too high");
        fee = _newFee;
    }

    function receiveMessage(
        bytes32 /* _nonce */,
        bytes3 _source_chain,
        bytes32 _source,
        bytes32[] memory _contents
    ) public {
        require(msg.sender == portal, "!portal");
        require(
            _source == chiaSideLockerPuzzle && _source_chain == bytes3("xch"),
            "!source"
        );

        // todo
    }

    function withdrawFees(
        address _assetContract,
        address[] memory _receivers,
        uint256[] memory _amounts
    ) public onlyOwner {
        require(_receivers.length == _amounts.length, "!length");

        for (uint256 i = 0; i < _receivers.length; i++) {
            fees[_assetContract] -= _amounts[i];

            SafeERC20.safeTransfer(
                IERC20(_assetContract),
                _receivers[i],
                _amounts[i]
            );
        }
    }

    function withdrawFees(
        address[] _assetIds,
        bytes32 _receiver
    ) public onlyOwner {
        // todo
    }
}
