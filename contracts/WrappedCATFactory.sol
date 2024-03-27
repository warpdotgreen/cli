// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "./interfaces/IPortal.sol";
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

    function _bridgeBack(
        bytes32 _assetId,
        bytes32 _receiver,
        uint256 _amount,
        uint256 _portalFee
    ) internal {
        bytes32[] memory message = new bytes32[](3);
        message[0] = _assetId;
        message[1] = _receiver;
        message[2] = bytes32(_amount);

        IPortal(portal).sendMessage{value: _portalFee}(
            bytes3("xch"),
            chiaSideUnlockerPuzzle,
            messageContents
        );
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

        WrappedCATInfo info = wrappedCATInfos[_contents[0]];
        uint256 amount = uint256(_contents[1]);
        uint256 transferFee = (amount * fee) / 10000;
        fees[_contents[0]] += transferFee;
        amount -= transferFee;

        WrappedCAT(info.deploymentAddress).mint(
            address(uint160(uint256(_contents[2]))),
            amount * info.mojoToTokenRatio
        );
    }

    // todo:  bridge back function, fee abstraction

    function withdrawFees(
        bytes32[] _assetIds,
        bytes32 _receiver
    ) public payable onlyOwner {
        // withdraw fees by bridging assets back to Chia
        uint256 portalFee = IPortal(portal).messageFee();
        require(msg.value == portalFee * _assetIds.length, "!fee");

        for (uint256 i = 0; i < _assetIds.length; i++) {
            _bridgeBack(_assetIds[i], _receiver, fees[_assetIds[i]], portalFee);

            fees[_assetIds[i]] = 0;
        }
    }
}
