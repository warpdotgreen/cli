// SPDX-License-Identifier: MIT License
/* yak tracks all over the place */

pragma solidity >=0.8.20;

interface IWETH {
    function deposit() external payable;

    function withdraw(uint) external;
}
