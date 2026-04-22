// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

import "./MaatToken.sol";

/// @title Vesting — Linear token vesting with cliff support for $MAAT allocations
/// @notice Allows an admin to create time-locked vesting schedules for beneficiaries.
///         Supports linear vesting with an optional cliff period and revocable schedules.
contract Vesting {

    // ─── References ──────────────────────────────────────────────────────────

    MaatToken public immutable maatToken;
    address   public           owner;

    // ─── Types ───────────────────────────────────────────────────────────────

    struct VestingSchedule {
        address beneficiary;   // Recipient of vested tokens
        uint256 totalAmount;   // Total $MAAT to vest (in wei)
        uint256 cliffEnd;      // Timestamp: no tokens releasable before this
        uint256 vestingEnd;    // Timestamp: fully vested at this point
        uint256 released;      // Cumulative $MAAT already released
        bool    revocable;     // Whether admin can revoke this schedule
        bool    revoked;       // True if schedule has been revoked
    }

    // ─── Storage ─────────────────────────────────────────────────────────────

    uint256 public scheduleCount;
    mapping(uint256 => VestingSchedule) public schedules;

    // ─── Events ──────────────────────────────────────────────────────────────

    event ScheduleCreated(
        uint256 indexed scheduleId,
        address indexed beneficiary,
        uint256 totalAmount,
        uint256 cliffEnd,
        uint256 vestingEnd,
        bool    revocable
    );

    event TokensReleased(
        uint256 indexed scheduleId,
        address indexed beneficiary,
        uint256 amount
    );

    event ScheduleRevoked(
        uint256 indexed scheduleId,
        address indexed beneficiary,
        uint256 reclaimedAmount
    );

    // ─── Constructor ─────────────────────────────────────────────────────────

    /// @param _maatToken Address of the deployed MaatToken contract.
    constructor(address _maatToken) {
        require(_maatToken != address(0), "Zero address");
        maatToken = MaatToken(_maatToken);
        owner = msg.sender;
    }

    // ─── Admin Functions ─────────────────────────────────────────────────────

    /// @notice Create a new vesting schedule for a beneficiary.
    /// @dev    The caller (owner) must have approved this contract to transfer `totalAmount`
    ///         tokens via MaatToken.approve() before calling this function.
    /// @param  beneficiary  Address that will receive vested tokens.
    /// @param  totalAmount  Total $MAAT to vest (in wei).
    /// @param  cliffEnd     Unix timestamp before which no tokens may be released.
    ///                      Must be >= block.timestamp.
    /// @param  vestingEnd   Unix timestamp at which all tokens are fully vested.
    ///                      Must be > cliffEnd.
    /// @param  revocable    Whether the owner may revoke the schedule before it completes.
    /// @return scheduleId   The ID of the newly created schedule.
    function createSchedule(
        address beneficiary,
        uint256 totalAmount,
        uint256 cliffEnd,
        uint256 vestingEnd,
        bool    revocable
    ) external onlyOwner returns (uint256 scheduleId) {
        require(beneficiary != address(0),           "Zero beneficiary");
        require(totalAmount  > 0,                    "Amount must be > 0");
        require(cliffEnd     >= block.timestamp,     "Cliff must be in future");
        require(vestingEnd   > cliffEnd,             "Vesting must end after cliff");

        // Transfer tokens from owner into this contract to hold in escrow
        require(
            maatToken.transferFrom(msg.sender, address(this), totalAmount),
            "Token transfer failed"
        );

        scheduleCount++;
        scheduleId = scheduleCount;

        schedules[scheduleId] = VestingSchedule({
            beneficiary: beneficiary,
            totalAmount: totalAmount,
            cliffEnd:    cliffEnd,
            vestingEnd:  vestingEnd,
            released:    0,
            revocable:   revocable,
            revoked:     false
        });

        emit ScheduleCreated(scheduleId, beneficiary, totalAmount, cliffEnd, vestingEnd, revocable);
    }

    /// @notice Revoke a vesting schedule, reclaiming unvested tokens.
    /// @dev    Releases any currently vested (but unreleased) tokens to the beneficiary first,
    ///         then returns the remaining unvested tokens to the owner.
    ///         Only callable by owner; schedule must be marked revocable and not yet revoked.
    /// @param  scheduleId The ID of the schedule to revoke.
    function revoke(uint256 scheduleId) external onlyOwner {
        VestingSchedule storage s = schedules[scheduleId];
        require(!s.revoked,    "Already revoked");
        require(s.revocable,   "Schedule not revocable");

        // Release any currently vested tokens to beneficiary before revoking
        uint256 releasable = _releasableAmount(s);
        if (releasable > 0) {
            s.released += releasable;
            require(maatToken.transfer(s.beneficiary, releasable), "Release failed");
            emit TokensReleased(scheduleId, s.beneficiary, releasable);
        }

        // Reclaim remaining unvested tokens
        uint256 reclaim = s.totalAmount - s.released;
        s.revoked = true;

        if (reclaim > 0) {
            require(maatToken.transfer(owner, reclaim), "Reclaim failed");
        }

        emit ScheduleRevoked(scheduleId, s.beneficiary, reclaim);
    }

    // ─── Beneficiary Functions ───────────────────────────────────────────────

    /// @notice Release all currently vested tokens for a schedule to the beneficiary.
    /// @dev    Reverts if no tokens are currently releasable (cliff not reached or all released).
    ///         Can be called by anyone; tokens always go to the schedule's beneficiary.
    /// @param  scheduleId The ID of the vesting schedule.
    function release(uint256 scheduleId) external {
        VestingSchedule storage s = schedules[scheduleId];
        require(!s.revoked, "Schedule revoked");

        uint256 releasable = _releasableAmount(s);
        require(releasable > 0, "No tokens releasable");

        s.released += releasable;
        require(maatToken.transfer(s.beneficiary, releasable), "Transfer failed");

        emit TokensReleased(scheduleId, s.beneficiary, releasable);
    }

    // ─── Views ───────────────────────────────────────────────────────────────

    /// @notice Returns the amount of tokens currently releasable for a schedule.
    /// @dev    Returns 0 if cliff has not been reached or schedule is revoked.
    /// @param  scheduleId The vesting schedule ID.
    /// @return The amount of $MAAT (in wei) that can be released right now.
    function releasableAmount(uint256 scheduleId) external view returns (uint256) {
        VestingSchedule storage s = schedules[scheduleId];
        if (s.revoked) return 0;
        return _releasableAmount(s);
    }

    /// @notice Returns the total amount of tokens that have vested as of now.
    /// @dev    Returns 0 before the cliff; returns totalAmount after vestingEnd.
    ///         Linear interpolation between cliffEnd and vestingEnd.
    /// @param  scheduleId The vesting schedule ID.
    /// @return The total cumulative vested amount (including already-released tokens).
    function vestedAmount(uint256 scheduleId) external view returns (uint256) {
        VestingSchedule storage s = schedules[scheduleId];
        return _vestedAmount(s);
    }

    // ─── Internal ────────────────────────────────────────────────────────────

    function _releasableAmount(VestingSchedule storage s) internal view returns (uint256) {
        return _vestedAmount(s) - s.released;
    }

    function _vestedAmount(VestingSchedule storage s) internal view returns (uint256) {
        if (block.timestamp < s.cliffEnd) {
            return 0;
        }
        if (block.timestamp >= s.vestingEnd) {
            return s.totalAmount;
        }
        // Linear vesting between cliffEnd and vestingEnd
        uint256 elapsed  = block.timestamp - s.cliffEnd;
        uint256 duration = s.vestingEnd   - s.cliffEnd;
        return s.totalAmount * elapsed / duration;
    }

    // ─── Admin ───────────────────────────────────────────────────────────────

    /// @notice Transfer ownership of this contract to a new address.
    /// @param  newOwner The address to transfer ownership to.
    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "Zero address");
        owner = newOwner;
    }

    // ─── Modifiers ───────────────────────────────────────────────────────────

    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }
}
