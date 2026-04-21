// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title MaatToken — $MAAT ERC-20 token for the MaatProof protocol
/// @notice Provides staking (deploy rights), validator rewards, slashing,
///         governance voting weight, and deploy-fee burning.
contract MaatToken {

    // ─── ERC-20 Storage ──────────────────────────────────────────────────────

    string  public constant name     = "MaatProof Token";
    string  public constant symbol   = "MAAT";
    uint8   public constant decimals = 18;

    uint256 public totalSupply;

    mapping(address => uint256)                     public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    // ─── Staking Storage ─────────────────────────────────────────────────────

    struct StakeRecord {
        uint256 amount;
        uint256 lockedUntil;    // timestamp; 0 = no lock
        bool    active;
    }

    mapping(address => StakeRecord) public stakes;

    // ─── Protocol Storage ────────────────────────────────────────────────────

    address public slashingContract;
    address public owner;           // governance / multisig
    uint256 public totalBurned;
    uint256 public totalSlashed;

    // ─── Events ──────────────────────────────────────────────────────────────

    event Transfer(address indexed from, address indexed to, uint256 value);
    event Approval(address indexed owner_, address indexed spender, uint256 value);

    event Staked(address indexed staker, uint256 amount, uint256 lockedUntil);
    event Unstaked(address indexed staker, uint256 amount);
    event Slashed(address indexed slashed, uint256 amount, address indexed slashingContract_);
    event FeeBurned(address indexed payer, uint256 amount);
    event SlashingContractUpdated(address indexed newContract);

    // ─── Constructor ─────────────────────────────────────────────────────────

    /// @param initialSupply Total $MAAT minted at genesis (e.g., 1_000_000_000e18)
    constructor(uint256 initialSupply) {
        owner       = msg.sender;
        totalSupply = initialSupply;
        balanceOf[msg.sender] = initialSupply;
        emit Transfer(address(0), msg.sender, initialSupply);
    }

    // ─── ERC-20 Core ─────────────────────────────────────────────────────────

    function transfer(address to, uint256 amount) external returns (bool) {
        _transfer(msg.sender, to, amount);
        return true;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        emit Approval(msg.sender, spender, amount);
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        require(allowance[from][msg.sender] >= amount, "Insufficient allowance");
        allowance[from][msg.sender] -= amount;
        _transfer(from, to, amount);
        return true;
    }

    // ─── Staking ─────────────────────────────────────────────────────────────

    /// @notice Stake $MAAT to earn deploy rights or validator attestation rights.
    /// @param amount     Amount of $MAAT to stake (in wei).
    /// @param lockPeriod Seconds to lock the stake (0 = no lock; min for validators = 21 days).
    function stake(uint256 amount, uint256 lockPeriod) external {
        require(amount > 0, "Amount must be > 0");
        require(balanceOf[msg.sender] >= amount, "Insufficient balance");

        // Add to existing stake if already staking
        StakeRecord storage rec = stakes[msg.sender];
        rec.amount     += amount;
        rec.lockedUntil = block.timestamp + lockPeriod;
        rec.active      = true;

        _transfer(msg.sender, address(this), amount);
        emit Staked(msg.sender, amount, rec.lockedUntil);
    }

    /// @notice Unstake $MAAT after the lock period expires.
    /// @param amount Amount to unstake (partial unstake supported).
    function unstake(uint256 amount) external {
        StakeRecord storage rec = stakes[msg.sender];
        require(rec.active,                             "No active stake");
        require(rec.amount >= amount,                   "Amount exceeds stake");
        require(block.timestamp >= rec.lockedUntil,     "Stake is still locked");

        rec.amount -= amount;
        if (rec.amount == 0) rec.active = false;

        _transfer(address(this), msg.sender, amount);
        emit Unstaked(msg.sender, amount);
    }

    /// @notice Returns the staked amount for an address.
    function stakedBalanceOf(address account) external view returns (uint256) {
        return stakes[account].amount;
    }

    // ─── Governance Voting Weight ────────────────────────────────────────────

    /// @notice Voting weight = staked balance (tokens in active stakes).
    /// @dev Governance contracts should call this to determine vote weight.
    function votingWeight(address account) external view returns (uint256) {
        return stakes[account].active ? stakes[account].amount : 0;
    }

    // ─── Slashing ────────────────────────────────────────────────────────────

    /// @notice Slash a staker's balance. Only callable by the slashing contract.
    /// @param target The address to slash.
    /// @param amount The amount of $MAAT to slash (in wei).
    function slash(address target, uint256 amount) external onlySlashingContract {
        StakeRecord storage rec = stakes[target];
        require(rec.active,           "No active stake to slash");
        require(rec.amount >= amount, "Slash amount exceeds stake");

        rec.amount -= amount;
        if (rec.amount == 0) rec.active = false;

        // Remove from this contract's held balance (slash burns the tokens)
        totalSupply  -= amount;
        totalSlashed += amount;

        emit Slashed(target, amount, msg.sender);
        emit Transfer(address(this), address(0), amount);
    }

    // ─── Deploy Fee Burn ─────────────────────────────────────────────────────

    /// @notice Burn $MAAT as a deploy fee. Called by the deployment protocol.
    /// @param payer  The agent or account paying the fee.
    /// @param amount Amount to burn (in wei).
    function burnDeployFee(address payer, uint256 amount) external onlySlashingContract {
        require(balanceOf[payer] >= amount, "Insufficient balance for fee");
        balanceOf[payer] -= amount;
        totalSupply      -= amount;
        totalBurned      += amount;

        emit FeeBurned(payer, amount);
        emit Transfer(payer, address(0), amount);
    }

    // ─── Admin ───────────────────────────────────────────────────────────────

    /// @notice Set the slashing contract address (owner only).
    function setSlashingContract(address _slashingContract) external onlyOwner {
        require(_slashingContract != address(0), "Zero address");
        slashingContract = _slashingContract;
        emit SlashingContractUpdated(_slashingContract);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        require(newOwner != address(0), "Zero address");
        owner = newOwner;
    }

    // ─── Internal ────────────────────────────────────────────────────────────

    function _transfer(address from, address to, uint256 amount) internal {
        require(balanceOf[from] >= amount, "Insufficient balance");
        balanceOf[from] -= amount;
        balanceOf[to]   += amount;
        emit Transfer(from, to, amount);
    }

    // ─── Modifiers ───────────────────────────────────────────────────────────

    modifier onlySlashingContract() {
        require(msg.sender == slashingContract, "Not slashing contract");
        _;
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }
}
