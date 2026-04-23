# User Authentication — Technical Specification

<!-- Addresses EDGE-AUTH-001 through EDGE-AUTH-075 (spec-edge-case-tester results for issue #124) -->

## Overview

This specification defines the **OAuth2 + MFA authentication system** for MaatProof's
human user layer.  It covers the OAuth2 Authorization Code Flow with PKCE, multi-factor
authentication (TOTP/WebAuthn), token lifecycle management, session handling, account
security controls, PostgreSQL schema, FastAPI endpoint contracts, audit logging
integration, and regulatory compliance mapping.

**Tech Stack**: Python 3.11+ · FastAPI · PostgreSQL 15+ · SQLAlchemy 2.x (async)
**OAuth2 standard**: RFC 6749 (Authorization Code + PKCE per RFC 7636)
**MFA standards**: TOTP per RFC 6238, WebAuthn Level 2 (W3C), FIDO2
**Token format**: JWT (RS256) — NOT HS256 (see §6 §JWT Algorithm Requirements)
**Password hashing**: Argon2id (RFC 9106) — NOT MD5/SHA-1/bcrypt-weak

This spec is the authoritative reference for:
- `specs/api-spec.md` human authentication endpoints (distinct from agent Ed25519 auth)
- `docs/06-security-model.md` human-layer threat mitigations
- `specs/audit-logging-spec.md` auth-specific event types

---

## §1 — OAuth2 Authorization Code Flow with PKCE

<!-- Addresses EDGE-AUTH-001, EDGE-AUTH-005, EDGE-AUTH-016, EDGE-AUTH-017,
     EDGE-AUTH-018, EDGE-AUTH-019, EDGE-AUTH-026, EDGE-AUTH-027, EDGE-AUTH-028,
     EDGE-AUTH-029 -->

### 1.1 Flow Overview

```mermaid
sequenceDiagram
    participant Browser
    participant FastAPI as FastAPI Auth Service
    participant OAuth as OAuth2 Provider\n(GitHub / Google / OIDC)
    participant PG as PostgreSQL
    participant Audit as Audit Log

    Browser->>FastAPI: GET /auth/login?provider=github
    FastAPI->>FastAPI: Generate state (UUID4, 32 bytes entropy)\n+ code_verifier (64 random bytes)\n+ code_challenge = BASE64URL(SHA256(code_verifier))
    FastAPI->>PG: Store (state, code_verifier, created_at) — TTL 10 min
    FastAPI-->>Browser: 302 → Provider /authorize?code_challenge=...&state=...

    Browser->>OAuth: Follow redirect
    OAuth->>Browser: Show consent screen
    Browser->>OAuth: User grants consent
    OAuth-->>Browser: 302 → /auth/callback?code=...&state=...

    Browser->>FastAPI: GET /auth/callback?code=...&state=...
    FastAPI->>PG: Lookup state; verify not expired, not reused
    FastAPI->>OAuth: POST /token (code + code_verifier)
    OAuth-->>FastAPI: access_token + id_token
    FastAPI->>FastAPI: Verify id_token signature
    FastAPI->>PG: Upsert user record
    FastAPI->>Audit: Log AUTH_OAUTH_CALLBACK_SUCCESS
    FastAPI-->>Browser: 302 → /auth/mfa (if MFA required) or set session cookie
```

### 1.2 PKCE Requirements (Addresses EDGE-AUTH-016)

| Parameter | Requirement |
|---|---|
| `code_verifier` | 64 cryptographically random bytes, BASE64URL-encoded, NOT URL-safe without padding stripped |
| `code_challenge` | `BASE64URL(SHA-256(ASCII(code_verifier)))` — S256 method ONLY |
| `code_challenge_method` | MUST be `S256`; `plain` method is **not permitted** |
| Storage | `code_verifier` stored server-side, keyed by `state`; never sent to browser |
| Expiry | State + code_verifier pair expires after 10 minutes; expired pairs are rejected with 400 |

> **EDGE-AUTH-016 — PKCE not enforced**: The token exchange endpoint MUST reject
> requests where `code_challenge_method=plain` or where the `code_verifier` is absent.
> This prevents authorization code interception attacks.

### 1.3 State Parameter / CSRF Protection (Addresses EDGE-AUTH-017)

- `state` MUST be a cryptographically random UUID4 (32 bytes minimum entropy).
- Stored in PostgreSQL `oauth_states` table with a 10-minute TTL.
- On callback: `state` from query parameter MUST match stored state; mismatch → `400 INVALID_STATE`.
- State is **single-use**: consumed immediately upon callback receipt; a second callback with the same state returns `400 STATE_ALREADY_USED`.
- States older than 10 minutes are rejected even if the signature matches.

> **EDGE-AUTH-005 — State collision under high load**: The `oauth_states` table has a
> UNIQUE constraint on `state`. Race-condition inserts with duplicate state values are
> rejected by the database; the caller retries with a new state. Probability of collision
> with a UUID4 is cryptographically negligible.

### 1.4 Redirect URI Validation (Addresses EDGE-AUTH-018, EDGE-AUTH-026)

- Permitted redirect URIs are an **allowlist** stored in the `oauth_providers` configuration table.
- The `redirect_uri` in every authorization request and token exchange MUST exactly match an allowlisted URI (byte-for-byte, no trailing slashes, no path normalization).
- Any URI not in the allowlist: request is rejected with `400 REDIRECT_URI_NOT_PERMITTED`.
- Open redirect attack mitigation: the post-login `next` parameter is validated against the same allowlist; non-allowlisted values are silently replaced with `/dashboard`.

### 1.5 Authorization Code Single-Use (Addresses EDGE-AUTH-027)

- Authorization codes are single-use per the OAuth2 spec (RFC 6749 §4.1.2).
- The `code` value is stored in the `oauth_codes` table with `used_at = NULL`.
- On exchange: `used_at` is set atomically; a second exchange attempt returns `400 CODE_ALREADY_USED`.
- Used codes are retained for 24 hours for audit purposes, then purged.

### 1.6 Scope Enforcement (Addresses EDGE-AUTH-028)

- Permitted scopes are defined per OAuth2 provider in configuration.
- Tokens returned by the provider are inspected; any scope not explicitly requested during the authorization request is rejected.
- Scope escalation in the token exchange response causes the flow to fail with `AUTH_SCOPE_ESCALATION` audit event.

### 1.7 Implicit Grant Flow Prohibition (Addresses EDGE-AUTH-029)

- The **Implicit Grant Flow** (response_type=token) is **prohibited**.
- The Authorization Code Flow with PKCE is the only permitted flow.
- Any request to `/auth/login` with `response_type=token` is rejected with `400 IMPLICIT_FLOW_NOT_PERMITTED`.
- This applies to all OAuth2 providers regardless of their supported flows.

---

## §2 — Multi-Factor Authentication (MFA)

<!-- Addresses EDGE-AUTH-031 through EDGE-AUTH-045 -->

### 2.1 Supported MFA Methods

| Method | Standard | Recommendation |
|---|---|---|
| TOTP | RFC 6238 (30-second window, SHA-1 HMAC, 6 digits) | Required for all users |
| WebAuthn / FIDO2 | W3C WebAuthn Level 2 | Preferred; phishing-resistant |
| SMS OTP | Vendor-specific | Discouraged; see §2.10 on SIM swapping |
| Recovery codes | Proprietary | Fallback only; one-time use |

### 2.2 TOTP Single-Use Enforcement (Addresses EDGE-AUTH-031, EDGE-AUTH-044)

- Every TOTP code verification records the `(user_id, totp_counter)` pair in `totp_used_codes`.
- **Same code cannot be used twice**, even within the same 30-second window.
- Schema:

```sql
CREATE TABLE totp_used_codes (
    user_id     UUID       NOT NULL REFERENCES users(id),
    totp_window BIGINT     NOT NULL,  -- floor(unix_timestamp / 30)
    used_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, totp_window)
);
```

- On verification: `INSERT INTO totp_used_codes … ON CONFLICT DO NOTHING` — if 0 rows inserted, the code was already used; return `401 MFA_CODE_ALREADY_USED`.
- Entries older than 90 seconds (3 windows) are eligible for cleanup.

> **EDGE-AUTH-044 — Previous window replay**: The `totp_window` stores the window
> counter, not the code itself. Both the current window and the prior window are
> checked (clock skew tolerance; see §2.3), but each window is single-use per user.

### 2.3 TOTP Clock Skew (Addresses EDGE-AUTH-032)

- The server accepts codes from the **current window ±1 window** (i.e., ±30 seconds drift).
- Clock skew beyond ±30 seconds is rejected with `401 MFA_CLOCK_SKEW`.
- Server MUST use an NTP-synchronized clock (max drift ±1 second).
- The tolerance window MUST NOT exceed ±1 step (per RFC 6238 §5.2 recommendation).

### 2.4 MFA Enrollment (Addresses EDGE-AUTH-037)

- MFA enrollment requires a currently valid, unexpired session.
- Enrolling a new MFA method does NOT invalidate existing sessions.
- After enrollment, the user must complete at least one successful MFA verification before MFA is marked `active` for that method.
- New MFA enrollment is recorded in the audit log as `AUTH_MFA_ENROLLED`.

### 2.5 MFA Disable Protection (Addresses EDGE-AUTH-038)

- MFA can only be disabled via a flow that requires:
  1. Current valid session (active `access_token`).
  2. Successful re-authentication (password re-entry) within the last 5 minutes.
  3. Successful MFA code verification using the method being disabled.
- Disabling MFA is recorded as `AUTH_MFA_DISABLED` with `actor_id = user_id`.
- An email notification is sent to the user's registered email on MFA disable.

### 2.6 MFA Brute Force Protection (Addresses EDGE-AUTH-039)

| Threshold | Action |
|---|---|
| 3 failed MFA attempts within 5 minutes | Temporary lockout: 15-minute cooldown |
| 10 failed MFA attempts within 60 minutes | Account suspended; email + audit alert |
| 50 failed MFA attempts in 24 hours (per IP) | IP rate-limited; security alert |

- MFA attempt counts are stored per `(user_id, ip_address)` in `mfa_attempt_log`.
- Lockout state is stored in PostgreSQL with a TTL enforced by `locked_until` timestamp.
- The `mfa_attempt_log` is retained for 90 days for security investigation.

### 2.7 MFA Code After Session Timeout (Addresses EDGE-AUTH-040)

- The MFA verification step is gated by a **pending-MFA session token** (a short-lived, opaque token issued after password/OAuth2 authentication, valid for 10 minutes).
- If the pending-MFA session token has expired before the user submits their MFA code, the server returns `401 MFA_SESSION_EXPIRED` and the user must restart the login flow.
- The pending-MFA token is distinct from the full access token; it confers NO access to protected resources.

### 2.8 No MFA Method Configured (Addresses EDGE-AUTH-041)

- **For new accounts**: MFA enrollment is mandatory before accessing protected resources. Users are redirected to `/auth/mfa/enroll` until at least one MFA method is active.
- **Admin exception**: Administrators may configure a per-tenant grace period (default 0 days) during which new users can access non-critical resources without MFA. This grace period MUST be explicitly configured; the default is no grace period.
- **API clients**: Service accounts using client_credentials flow are exempt from TOTP-based MFA; they use mutual TLS + scoped API keys instead.

### 2.9 Recovery Codes (Addresses EDGE-AUTH-033, EDGE-AUTH-042, EDGE-AUTH-043)

#### Recovery Code Issuance
- At MFA enrollment, **10 recovery codes** are generated: each 16 characters, BASE32 alphanumeric, grouped as `XXXX-XXXX-XXXX-XXXX`.
- Recovery codes are shown **once** at enrollment and are hashed (Argon2id) before storage.
- Users MUST acknowledge they have saved recovery codes (explicit UI checkbox); enrollment is not complete until acknowledged.

#### Recovery Code Use
- Each recovery code is **single-use**: after use, it is deleted from storage.
- Use of a recovery code is recorded as `AUTH_RECOVERY_CODE_USED` in the audit log.
- After a recovery code is used, the user is immediately directed to enroll a new primary MFA method.

#### All Recovery Codes Exhausted (Addresses EDGE-AUTH-042)
- When a user has 0 remaining recovery codes AND cannot verify their primary MFA method, the account enters `MFA_RECOVERY_REQUIRED` state.
- Access to protected resources is blocked until account recovery is completed.
- Recovery requires an **out-of-band identity verification** process; see §8 Account Recovery.

#### TOTP App Loss (Addresses EDGE-AUTH-043)
- If a user's TOTP app is uninstalled or the authenticator device is lost:
  - User can log in using recovery codes.
  - After using a recovery code, user re-enrolls a new TOTP device.
  - If no recovery codes remain, see §8 Account Recovery.

### 2.10 SMS-Based MFA Risks (Addresses EDGE-AUTH-034, EDGE-AUTH-035)

- **SMS OTP** is supported as a fallback method for compatibility but is explicitly marked as the **least secure** option in the user-facing UI.
- SMS OTP MUST NOT be the only MFA method for accounts with `role:admin` or `role:release`.
- **SIM Swapping Mitigation**: SMS OTP delivery is rate-limited to 3 codes per 10-minute window per phone number. Repeated code requests trigger a security alert email to the account's registered email address.
- After 5 consecutive SMS OTP delivery failures to a number (within 24 hours), the phone number is flagged for manual review. The account is not locked out but an alert is sent.
- See §8 Account Recovery for the protocol when SMS delivery is compromised.

> **NOTE — SIM Swapping (EDGE-AUTH-035)**: Full SIM-swapping attack mitigation requires
> coordination with the telecom provider and is out of scope for this spec. The mitigations
> here (rate limiting, alerting) reduce the attack surface but do not eliminate the risk.
> Users should be encouraged to migrate to FIDO2/WebAuthn where SIM swapping is not a threat.

### 2.11 TOTP Secret Storage (Addresses EDGE-AUTH-036)

- TOTP secrets MUST be encrypted at rest using AES-256-GCM with a key stored in the configured KMS (Azure Key Vault / AWS KMS / GCP KMS).
- Plaintext TOTP secrets MUST NOT be stored in the database.
- Schema:

```sql
CREATE TABLE user_mfa_totp (
    id                UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID          NOT NULL REFERENCES users(id),
    encrypted_secret  BYTEA         NOT NULL,    -- AES-256-GCM encrypted
    kms_key_version   TEXT          NOT NULL,    -- KMS key version used for encryption
    enrolled_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    last_used_at      TIMESTAMPTZ,
    is_active         BOOLEAN       NOT NULL DEFAULT FALSE,
    UNIQUE (user_id)
);
```

- On KMS key rotation, `encrypted_secret` is re-encrypted with the new key version and `kms_key_version` is updated.

---

## §3 — Token Management

<!-- Addresses EDGE-AUTH-046 through EDGE-AUTH-055 -->

### 3.1 Access Token Specification

| Property | Value | Rationale |
|---|---|---|
| Format | JWT | RFC 7519 |
| Algorithm | RS256 (RSA-PKCS1v15 with SHA-256) | EDGE-AUTH-021: prevents algorithm confusion attacks |
| NOT HS256 | Never | EDGE-AUTH-021: symmetric key attacks |
| NOT `none` | Never | EDGE-AUTH-022: unsigned JWT attacks |
| Expiry (`exp`) | 15 minutes from `iat` | Short-lived to limit exposure |
| Issued-At (`iat`) | Server UTC time | MUST be ≤ `now()` — future `iat` is rejected (EDGE-AUTH-052) |
| Subject (`sub`) | `user_id` (UUID4) | NOT email address (EDGE-AUTH-049) |
| Audience (`aud`) | Service identifier string (e.g., `maatproof-api`) | Prevents cross-service token use |
| Issuer (`iss`) | `https://auth.maatproof.dev` | Verified on every request |
| `nonce` | UUID4 | Replay prevention (5-minute TTL in `token_nonces` table) |

> **EDGE-AUTH-049 — PII in JWT payload**: The JWT payload MUST NOT contain PII (email, name,
> phone). The `sub` claim contains only a user UUID. PII lookup is performed via the `users`
> table on the server side.

> **EDGE-AUTH-021/022 — Algorithm confusion attacks**: The JWT signing library MUST be
> configured to accept ONLY `RS256`. Requests with `alg: HS256` or `alg: none` are rejected
> at the middleware level with `401 INVALID_TOKEN_ALGORITHM`.

### 3.2 Access Token Expiry (Addresses EDGE-AUTH-047)

- Access token `exp` MUST be a positive integer representing a Unix timestamp in the future.
- Token generation MUST reject `exp <= iat` or `exp - iat > 3600` (max 1 hour).
- Default TTL: **15 minutes** for standard sessions, **5 minutes** for elevated-privilege operations.

### 3.3 Refresh Token Specification (Addresses EDGE-AUTH-048, EDGE-AUTH-050, EDGE-AUTH-024)

```sql
CREATE TABLE refresh_tokens (
    id              UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID          NOT NULL REFERENCES users(id),
    token_hash      TEXT          NOT NULL UNIQUE,   -- SHA-256 of the raw token
    issued_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ   NOT NULL,          -- MUST be set; no infinite tokens
    revoked_at      TIMESTAMPTZ,                     -- NULL if still valid
    rotated_to      UUID          REFERENCES refresh_tokens(id),  -- chain for rotation audit
    session_id      UUID          NOT NULL,
    device_fingerprint TEXT,
    CONSTRAINT expires_set CHECK (expires_at IS NOT NULL),
    CONSTRAINT expiry_positive CHECK (expires_at > issued_at)
);
CREATE INDEX idx_rt_user_session ON refresh_tokens(user_id, session_id) WHERE revoked_at IS NULL;
```

- **No infinite-lifetime refresh tokens** (EDGE-AUTH-048): `expires_at` is mandatory; a NULL `expires_at` is rejected by the database constraint. Default TTL: 30 days.
- **Token rotation** (EDGE-AUTH-024): On each use, the old refresh token is immediately revoked (`revoked_at = NOW()`) and a new one is issued. The `rotated_to` field links the chain.
- **Single active token per session** (EDGE-AUTH-050): Each `session_id` may have at most one non-revoked, non-expired refresh token. Issuing a new refresh token without rotating (i.e., a second valid token for the same session) is a security error.
- **Refresh token theft detection** (EDGE-AUTH-024): If a revoked refresh token is presented, all refresh tokens for that `session_id` are immediately revoked and the user is logged out. An `AUTH_REFRESH_TOKEN_REUSE` audit event is emitted.

### 3.4 Token Invalidation on Logout (Addresses EDGE-AUTH-046)

- Logout MUST:
  1. Revoke the current refresh token (set `revoked_at`).
  2. Add the current access token `jti` to `token_blocklist` (TTL = remaining access token lifetime).
  3. Delete the session cookie.
- Access tokens MUST be validated against the blocklist on every request.
- The `token_blocklist` table uses a background cleanup job to remove expired entries.

```sql
CREATE TABLE token_blocklist (
    jti         TEXT        PRIMARY KEY,    -- JWT ID claim
    expires_at  TIMESTAMPTZ NOT NULL,       -- removed after this time
    revoked_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    reason      TEXT                        -- 'logout', 'key_rotation', 'security_revoke'
);
```

> **EDGE-AUTH-046 — Token not expiring on logout**: Without server-side blocklisting, a
> stolen access token remains valid until its `exp`. The blocklist ensures that logout
> immediately invalidates the token even within its 15-minute window.

### 3.5 Token Used After Account Deactivation (Addresses EDGE-AUTH-055)

- On account deactivation:
  1. All refresh tokens for the user are revoked.
  2. All active `jti` values are added to `token_blocklist`.
  3. The `users.status` field is set to `DEACTIVATED`.
- The token validation middleware checks `users.status` on every request; a DEACTIVATED user's token is rejected with `401 ACCOUNT_DEACTIVATED`.

### 3.6 Token Signing Key Rotation (Addresses EDGE-AUTH-051)

- JWT signing keys (RSA-2048) are rotated every **90 days**.
- The JWKS endpoint (`/auth/.well-known/jwks.json`) exposes all **active + recently retired** signing keys.
- During key rotation:
  1. A new RSA keypair is generated in the configured KMS.
  2. New tokens are signed with the new key; existing tokens remain valid until their `exp`.
  3. The old key is retained in JWKS for the duration of the maximum access token lifetime (15 minutes) before removal.
- Tokens signed with a fully-retired key (removed from JWKS) return `401 TOKEN_KEY_RETIRED`.

> **EDGE-AUTH-051 — Token signed with revoked key**: The rotation overlap window of 15 minutes
> ensures no valid, unexpired token is invalidated by a key rotation. After 15 minutes,
> all tokens issued with the old key will have expired naturally.

### 3.7 Token in URL Query Parameter (Addresses EDGE-AUTH-025)

- Access tokens MUST be transmitted only in the `Authorization: Bearer` header.
- Query parameter transmission (`?token=...`, `?access_token=...`) is **prohibited**.
- The API middleware logs a `AUTH_TOKEN_IN_QUERY_PARAM` warning and returns `400 TOKEN_IN_URL_PROHIBITED` if a token is detected in the query string.
- This prevents token leakage via Referer headers, browser history, and server access logs.

### 3.8 Token Introspection Endpoint Authentication (Addresses EDGE-AUTH-054)

- The token introspection endpoint (`POST /auth/introspect`) requires HTTP Basic authentication with a registered service client credential.
- Unauthenticated introspection requests return `401 CLIENT_AUTH_REQUIRED`.

### 3.9 Token Payload Size Limit (Addresses EDGE-AUTH-053)

- JWT payloads MUST NOT exceed 4,096 bytes.
- Custom claims in the JWT are limited to the minimum necessary (user_id, roles, tenant_id).
- Permissions are NOT embedded in the JWT; they are fetched from the database per-request.

---

## §4 — Session Management

<!-- Addresses EDGE-AUTH-007, EDGE-AUTH-013, EDGE-AUTH-014, EDGE-AUTH-030 -->

### 4.1 Session Model

```sql
CREATE TABLE user_sessions (
    id                  UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID          NOT NULL REFERENCES users(id),
    created_at          TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    last_active_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    expires_at          TIMESTAMPTZ   NOT NULL,
    ip_address          INET,
    user_agent          TEXT,
    revoked_at          TIMESTAMPTZ,
    tenant_id           UUID          NOT NULL REFERENCES tenants(id)
);
CREATE INDEX idx_sessions_user ON user_sessions(user_id) WHERE revoked_at IS NULL;
```

### 4.2 Multi-Device Concurrent Sessions (Addresses EDGE-AUTH-013)

- A user may have **up to 10 concurrent active sessions** (one per device/browser).
- Exceeding the limit revokes the oldest session (LRU eviction).
- Concurrent token refresh by the same user on multiple devices is permitted; each session has its own independent refresh token chain.

### 4.3 Multi-Tenancy Session Isolation (Addresses EDGE-AUTH-030)

- Every session is bound to a `tenant_id`.
- All API requests validate that the `tenant_id` in the JWT matches the resource's `tenant_id`.
- Cross-tenant token use: if a user's token carries `tenant_id = A` and attempts to access a `tenant_id = B` resource, the request is rejected with `403 CROSS_TENANT_ACCESS_DENIED`.
- `tenant_id` is included in the JWT payload as an opaque claim; it is NOT user-modifiable.

### 4.4 Database Write Isolation (Addresses EDGE-AUTH-014)

- Session creation and token issuance use PostgreSQL `SERIALIZABLE` isolation level.
- Concurrent writes to the same session (e.g., two simultaneous refresh requests) are handled by optimistic locking on `refresh_tokens.token_hash` (UNIQUE constraint).
- The second concurrent refresh attempt receives `409 CONFLICT_REFRESH_CONCURRENT`; the client retries with backoff.

---

## §5 — Database Schema (PostgreSQL)

<!-- Addresses EDGE-AUTH-056 through EDGE-AUTH-065 -->

### 5.1 Core Tables

```sql
-- Users table
CREATE TABLE users (
    id              UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    email           TEXT          NOT NULL UNIQUE,
    password_hash   TEXT,                          -- NULL for OAuth-only accounts
    status          TEXT          NOT NULL DEFAULT 'ACTIVE'
                                  CHECK (status IN ('ACTIVE', 'SUSPENDED', 'DEACTIVATED', 'MFA_RECOVERY_REQUIRED')),
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    tenant_id       UUID          NOT NULL REFERENCES tenants(id),
    failed_login_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until    TIMESTAMPTZ,
    last_login_at   TIMESTAMPTZ
);

-- OAuth provider links
CREATE TABLE user_oauth_accounts (
    id              UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID          NOT NULL REFERENCES users(id),
    provider        TEXT          NOT NULL,    -- 'github', 'google', etc.
    provider_user_id TEXT         NOT NULL,
    UNIQUE (provider, provider_user_id)
);

-- OAuth state table (PKCE / CSRF)
CREATE TABLE oauth_states (
    state           TEXT          PRIMARY KEY,
    code_verifier   TEXT          NOT NULL,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    used_at         TIMESTAMPTZ,
    redirect_uri    TEXT          NOT NULL,
    provider        TEXT          NOT NULL
);
-- Cleanup: states older than 15 minutes
CREATE INDEX idx_oauth_states_cleanup ON oauth_states(created_at) WHERE used_at IS NULL;
```

### 5.2 SQL Injection Prevention (Addresses EDGE-AUTH-056)

- All database queries MUST use **SQLAlchemy parameterized queries** or the ORM.
- Raw string interpolation in SQL queries is **prohibited** and enforced by code review.
- The `email` field uses a PostgreSQL `TEXT` type with a `UNIQUE` constraint; it is never interpolated into a query string.
- Email and username inputs are validated with a strict regex before database access.

### 5.3 Deleted User with Active Session (Addresses EDGE-AUTH-057)

- When a user record is deleted (soft delete: `status = DEACTIVATED`):
  1. All refresh tokens are revoked.
  2. All sessions are revoked.
  3. All access token `jti` values are blocklisted.
- Token validation middleware checks `users.status` before authorizing requests.
- Hard deletes (`DELETE FROM users`) are prohibited; use soft deletes only.

### 5.4 Connection String Exposure Prevention (Addresses EDGE-AUTH-058)

- Database connection strings are stored exclusively in environment variables or the configured KMS (never in code or config files checked into version control).
- Error responses from the API MUST NOT include database error messages, connection details, or stack traces.
- A global FastAPI exception handler catches `SQLAlchemyError` and returns a generic `500 INTERNAL_SERVER_ERROR` response, logging the full error server-side only.

### 5.5 Password Hashing Requirements (Addresses EDGE-AUTH-059)

```python
# Required: Argon2id with these parameters
from argon2 import PasswordHasher

ph = PasswordHasher(
    time_cost=3,        # iterations (minimum 2)
    memory_cost=65536,  # 64 MB (minimum 19456 per OWASP)
    parallelism=2,      # threads
    hash_len=32,
    salt_len=16,
    encoding='utf-8',
    type=argon2.Type.ID  # Argon2id REQUIRED
)
```

- **Prohibited algorithms**: MD5, SHA-1, SHA-256 (unsalted), bcrypt with cost < 12, PBKDF2 with < 310,000 iterations.
- Password hashes MUST be upgraded on next login if stored with a deprecated algorithm.
- The `password_hash` column stores the full Argon2id string including parameters and salt.

### 5.6 OAuth2 State Cleanup (Addresses EDGE-AUTH-064)

- A background job runs every 5 minutes to purge `oauth_states` records where `created_at < NOW() - INTERVAL '15 minutes'`.
- The purge is a hard delete (storage leak prevention).
- Authorization codes in `oauth_codes` older than 24 hours are also purged.

### 5.7 User Creation Race Condition (Addresses EDGE-AUTH-065)

- The `users.email` column has a `UNIQUE` constraint.
- If two concurrent OAuth2 callbacks attempt to create the same user, the second INSERT raises a `UniqueViolationError`.
- The application handles this by retrying with a `SELECT` (upsert pattern) using `ON CONFLICT (email) DO UPDATE SET last_login_at = NOW()`.
- This is safe because user creation is idempotent; the result is the same user record regardless of which INSERT wins.

### 5.8 Database Migration Safety (Addresses EDGE-AUTH-062)

- Database migrations are run via Alembic with `--transactional-ddl`.
- Migrations that drop columns or change column types MUST include a backward-compatible transition period (additive migrations only; breaking schema changes require a multi-migration sequence).
- Active sessions are NOT invalidated by schema migrations unless the migration explicitly revokes all sessions (requiring a maintenance window and user notification).

### 5.9 Backup and Session Security (Addresses EDGE-AUTH-063)

- Database backups include the `refresh_tokens` and `user_sessions` tables.
- If a backup is restored (e.g., disaster recovery), **all active sessions MUST be revoked** immediately after restoration.
- The restoration runbook MUST include the step: `UPDATE refresh_tokens SET revoked_at = NOW() WHERE revoked_at IS NULL;` and `UPDATE user_sessions SET revoked_at = NOW() WHERE revoked_at IS NULL;`
- This prevents sessions from a pre-restoration snapshot from being valid in the restored environment.

---

## §6 — Security Controls

<!-- Addresses EDGE-AUTH-015, EDGE-AUTH-019, EDGE-AUTH-020, EDGE-AUTH-021,
     EDGE-AUTH-022, EDGE-AUTH-023, EDGE-AUTH-025 -->

### 6.1 Account Lockout (Addresses EDGE-AUTH-015)

| Threshold | Action |
|---|---|
| 5 failed password attempts within 10 minutes | Temporary lockout: 15 minutes |
| 10 failed attempts within 30 minutes | Extended lockout: 60 minutes |
| 20 failed attempts within 24 hours | Account suspended; manual review required |

- Lockout state is stored in `users.locked_until`.
- Failed attempts are counted per `user_id`; IP-based counting is secondary.
- Lockout counter increment uses PostgreSQL `UPDATE … SET failed_login_attempts = failed_login_attempts + 1 … RETURNING *` to handle concurrent increments atomically (EDGE-AUTH-015).
- Successful login resets `failed_login_attempts = 0` and clears `locked_until`.

### 6.2 Referer Header Token Leakage (Addresses EDGE-AUTH-019)

- The `Referrer-Policy: no-referrer` header is set on all API responses.
- Auth callback pages set `Referrer-Policy: no-referrer` and include no sensitive data in their URLs.
- Tokens are transmitted ONLY in the `Authorization: Bearer` header (see §3.7).

### 6.3 JWT Signing Key Entropy (Addresses EDGE-AUTH-020)

- JWT signing keys are RSA-2048 generated in the configured HSM/KMS.
- Keys MUST NOT be stored in source code, `.env` files, or configuration management.
- Key generation uses the KMS's hardware random number generator.
- Minimum key strength: RSA-2048 (equivalent to 112-bit security).

### 6.4 JWT Algorithm Requirements (Addresses EDGE-AUTH-021, EDGE-AUTH-022)

```python
import jwt

# CORRECT — explicit algorithm allowlist
payload = jwt.decode(
    token,
    public_key,
    algorithms=["RS256"],  # ONLY RS256 permitted
    audience="maatproof-api",
    issuer="https://auth.maatproof.dev",
)

# PROHIBITED — never use algorithms=None or omit algorithms parameter
# payload = jwt.decode(token, public_key)  # vulnerable to alg:none attack
```

- The `algorithms` parameter MUST be an explicit allowlist: `["RS256"]`.
- HS256, HS384, HS512, RS384, RS512, ES256, PS256 are NOT permitted (to prevent algorithm confusion).
- `alg: none` is always rejected.

### 6.5 Token Replay After Logout (Addresses EDGE-AUTH-023)

- See §3.4 (Token Blocklist). Every logout operation blocklists the current `jti`.
- The `nonce` claim in access tokens provides additional replay prevention for the token's 15-minute lifetime.
- Nonces are validated against `token_nonces` table (TTL = 15 minutes).

---

## §7 — FastAPI Endpoint Contracts

<!-- Addresses EDGE-AUTH-001, EDGE-AUTH-002, EDGE-AUTH-003, EDGE-AUTH-004,
     EDGE-AUTH-006, EDGE-AUTH-007, EDGE-AUTH-008, EDGE-AUTH-009, EDGE-AUTH-010,
     EDGE-AUTH-071, EDGE-AUTH-072, EDGE-AUTH-073, EDGE-AUTH-074, EDGE-AUTH-075 -->

### 7.1 Endpoint List

| Method | Path | Description | Auth Required |
|---|---|---|---|
| `GET` | `/auth/login` | Initiate OAuth2 flow | No |
| `GET` | `/auth/callback` | OAuth2 provider callback | No |
| `POST` | `/auth/mfa/verify` | Submit MFA code | Pending-MFA token |
| `POST` | `/auth/mfa/enroll` | Enroll MFA method | Active session |
| `GET` | `/auth/mfa/enroll/totp` | Get TOTP QR code | Active session |
| `POST` | `/auth/mfa/disable` | Disable MFA method | Active session + re-auth |
| `POST` | `/auth/token/refresh` | Refresh access token | Refresh token |
| `POST` | `/auth/logout` | Revoke session | Active session |
| `GET` | `/auth/.well-known/jwks.json` | JWKS public keys | No |
| `POST` | `/auth/introspect` | Token introspection | Client credentials |

### 7.2 Rate Limiting (Addresses EDGE-AUTH-001, EDGE-AUTH-002, EDGE-AUTH-004)

| Endpoint | Limit | Window | Scope |
|---|---|---|---|
| `GET /auth/login` | 20 requests | 1 minute | Per IP |
| `GET /auth/callback` | 10 requests | 1 minute | Per IP |
| `POST /auth/mfa/verify` | 5 attempts | 5 minutes | Per user_id |
| `POST /auth/token/refresh` | 60 requests | 1 minute | Per user_id |
| `POST /auth/logout` | 10 requests | 1 minute | Per user_id |

- Rate limit state is stored in Redis (if available) or PostgreSQL.
- Rate limit exceeded returns `429 TOO_MANY_REQUESTS` with a `Retry-After` header.
- Rate limit counters are atomic (Redis INCR or PostgreSQL row-level locking).

> **EDGE-AUTH-002 — MFA storm**: The per-user rate limit on `/auth/mfa/verify` (5 attempts / 5 minutes)
> prevents brute-force enumeration of 6-digit TOTP codes. At 1 million possible codes and 1 attempt
> per 60 seconds, brute force would take ~11.5 days — exceeding the 30-second TOTP window by a factor
> of ~33,000.

> **EDGE-AUTH-004 — Token refresh storm**: On token expiry boundary (e.g., all tokens issued at the
> same `iat`), a burst of refresh requests may arrive simultaneously. The per-user limit of
> 60/minute prevents server overload. The 15-minute access token TTL is staggered by adding
> `random.uniform(0, 60)` seconds to each token's `exp` to spread the refresh burst.

### 7.3 Database Connection Pool Exhaustion (Addresses EDGE-AUTH-003)

- SQLAlchemy async connection pool: `pool_size=20`, `max_overflow=10`, `pool_timeout=5s`.
- If all connections are in use, new requests wait up to 5 seconds; timeout returns `503 SERVICE_UNAVAILABLE`.
- Connection pool metrics (active, idle, overflow) are exposed via `/metrics` (Prometheus format).
- Pool exhaustion is logged as `AUTH_DB_POOL_EXHAUSTED` and triggers a PagerDuty alert.

### 7.4 Concurrent OAuth2 Callbacks (Addresses EDGE-AUTH-009)

- Two simultaneous callbacks with the same `state` parameter: the second will find `used_at IS NOT NULL` in the `oauth_states` table and return `400 STATE_ALREADY_USED`.
- The UNIQUE constraint on `state` + the `used_at` update are performed in a single transaction with `SELECT FOR UPDATE`.

### 7.5 OAuth2 Provider Outage (Addresses EDGE-AUTH-071)

- If the OAuth2 provider is unavailable during token exchange:
  - The callback endpoint returns `503 OAUTH_PROVIDER_UNAVAILABLE` with a `Retry-After: 30` header.
  - The pending `oauth_states` record is NOT consumed (the user can retry).
  - A circuit breaker prevents cascading failures if the provider is down for > 60 seconds.
- Local-account authentication (password-based) remains available when OAuth2 providers are down.

### 7.6 PostgreSQL Failover During Token Validation (Addresses EDGE-AUTH-072)

- The FastAPI application uses SQLAlchemy with a PostgreSQL primary + read replica setup.
- If the primary is unavailable during a write operation (session creation, token revocation):
  - The request returns `503 DATABASE_UNAVAILABLE`.
  - Token refresh retains the current access token's validity until the primary recovers.
- The health check endpoint (`/health`) reports database connectivity status.

### 7.7 Key Rotation During Active Sessions (Addresses EDGE-AUTH-073)

- JWT signing key rotation (see §3.6) does NOT invalidate existing sessions.
- All existing access tokens remain valid until their `exp` (signed with the old key, which is still in JWKS during the overlap window).
- After the overlap window, all access tokens signed with the old key have expired naturally (15-minute TTL).
- The application handles `jwt.exceptions.InvalidSignatureError` for old-key tokens by returning `401 TOKEN_KEY_RETIRED` after the key is removed from JWKS.

### 7.8 MFA Notification Failure (Addresses EDGE-AUTH-074)

- If the SMS delivery service is unavailable when an SMS OTP is requested:
  - Return `503 MFA_DELIVERY_UNAVAILABLE` to the client.
  - The user is offered alternative MFA methods (TOTP, recovery code).
  - SMS delivery failures are logged as `AUTH_SMS_DELIVERY_FAILED`.
- If email notification for MFA disable confirmation fails:
  - The MFA disable operation proceeds (it has already been authorized).
  - The email failure is logged; a retry is attempted after 5 minutes.

### 7.9 FastAPI Process Crash (Addresses EDGE-AUTH-075)

- The FastAPI service runs behind a reverse proxy (nginx/Traefik) with health checks.
- In-flight authentication requests are not persisted in memory; all state is in PostgreSQL.
- On process restart, no in-flight state is lost (the `oauth_states` records in PostgreSQL are still valid).
- Pending-MFA session tokens are stored in PostgreSQL; they remain valid across restarts within their 10-minute TTL.

### 7.10 Bulk First-Login Flows (Addresses EDGE-AUTH-010)

- A bulk user import (e.g., 10,000 users) does NOT trigger simultaneous first-login flows.
- Users are imported with `status = 'ACTIVE'` but require email verification before first login.
- Email verification tokens are issued lazily (on first login attempt), not proactively on import.

---

## §8 — Account Recovery

<!-- Addresses EDGE-AUTH-042, EDGE-AUTH-043, EDGE-AUTH-045, EDGE-AUTH-074 -->

### 8.1 Recovery Flow

```mermaid
flowchart TD
    A[User cannot access MFA] --> B{Any recovery codes\nremaining?}
    B -->|Yes| C[Enter recovery code\nSee §2.9]
    B -->|No| D[Account enters\nMFA_RECOVERY_REQUIRED state]
    D --> E[User contacts support\nvia verified email or phone]
    E --> F[Support verifies identity\nvia out-of-band challenge]
    F --> G{Identity\nverified?}
    G -->|Yes| H[Support issues\none-time recovery link\n(TTL: 30 minutes)]
    G -->|No| I[Access denied\nAttempt logged]
    H --> J[User clicks link\n+ enters new email code]
    J --> K[Reset MFA enrollment\nGenerate new recovery codes]
    K --> L[Log AUTH_ACCOUNT_RECOVERED]
```

### 8.2 WebAuthn Authenticator Loss (Addresses EDGE-AUTH-045)

- If a user's FIDO2 authenticator is lost:
  - User can use a recovery code to bypass the lost authenticator.
  - After bypass, user is directed to enroll a new WebAuthn authenticator.
  - The old authenticator credential is deregistered.
- If no recovery codes remain, see §8.1 (contact support).

---

## §9 — Audit Logging Integration

<!-- Addresses EDGE-AUTH-066 through EDGE-AUTH-070 -->

All authentication events MUST be recorded in the tamper-evident audit log defined in
`specs/audit-logging-spec.md`. Auth events use the `actor_id` field to track the user DID
or user UUID.

### 9.1 Auth Event Types

| Event Name | Trigger | actor_id |
|---|---|---|
| `AUTH_LOGIN_SUCCESS` | Successful OAuth2 + MFA login | user UUID |
| `AUTH_LOGIN_FAILED` | Failed login attempt (wrong password, invalid OAuth2) | NULL (user not authenticated) |
| `AUTH_MFA_SUCCESS` | Successful MFA verification | user UUID |
| `AUTH_MFA_FAILED` | Failed MFA attempt | user UUID (if known) |
| `AUTH_MFA_CODE_REUSED` | Attempted reuse of TOTP code | user UUID |
| `AUTH_MFA_ENROLLED` | New MFA method enrolled | user UUID |
| `AUTH_MFA_DISABLED` | MFA method disabled | user UUID |
| `AUTH_RECOVERY_CODE_USED` | Recovery code consumed | user UUID |
| `AUTH_ACCOUNT_LOCKED` | Account locked due to failed attempts | NULL |
| `AUTH_ACCOUNT_RECOVERED` | Account recovery completed | user UUID |
| `AUTH_TOKEN_REFRESH` | Access token refreshed | user UUID |
| `AUTH_LOGOUT` | Session logged out | user UUID |
| `AUTH_OAUTH_CALLBACK_SUCCESS` | OAuth2 callback completed | NULL (user not yet authenticated) |
| `AUTH_SCOPE_ESCALATION` | Unexpected OAuth2 scope returned | NULL |
| `AUTH_REFRESH_TOKEN_REUSE` | Revoked refresh token reused | user UUID |
| `AUTH_TOKEN_IN_QUERY_PARAM` | Token detected in URL | user UUID (if decodeable) |
| `AUTH_SMS_DELIVERY_FAILED` | SMS OTP delivery failure | user UUID |
| `AUTH_DB_POOL_EXHAUSTED` | Connection pool exhausted | NULL (system event) |
| `AUTH_ACCOUNT_DEACTIVATED` | Account deactivated while tokens active | user UUID |

### 9.2 Failed Login Retention (Addresses EDGE-AUTH-068)

- `AUTH_LOGIN_FAILED` and `AUTH_MFA_FAILED` events MUST be retained for:
  - **SOC 2**: minimum 1 year
  - **HIPAA**: minimum 6 years
  - **SOX**: minimum 7 years
- See `specs/audit-logging-spec.md §9` for the full retention policy.

### 9.3 MFA Failure Security Alerts (Addresses EDGE-AUTH-067)

- When `mfa_attempt_log` exceeds the brute-force threshold (§2.6):
  - `AUTH_MFA_FAILED` events trigger a security alert to the security team.
  - Alert includes: user_id, IP address, attempt count, time window.
  - Alert is sent via the configured alerting channel (PagerDuty, Slack, email).

### 9.4 OAuth2 User Consent Recording (Addresses EDGE-AUTH-069)

- User consent for OAuth2 scopes is recorded in `oauth_consent_log`:

```sql
CREATE TABLE oauth_consent_log (
    id              UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID          NOT NULL REFERENCES users(id),
    provider        TEXT          NOT NULL,
    scopes_granted  TEXT[]        NOT NULL,
    consented_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    ip_address      INET,
    user_agent      TEXT
);
```

- This provides an audit trail for GDPR and other consent requirements.

### 9.5 Audit Log Correlation with Deployment Actions (Addresses EDGE-AUTH-070)

- A `correlation_id` field is added to the `audit_log.metadata` for auth events that lead to deployment actions.
- The `correlation_id` is a UUID4 that ties the human login event (`AUTH_LOGIN_SUCCESS`) to subsequent deployment approvals (`HUMAN_APPROVED`) in `specs/audit-logging-spec.md`.
- The `correlation_id` is propagated through the request context (FastAPI `Request.state.correlation_id`) and included in all audit log entries within the same request chain.

---

## §10 — Scale and Concurrency

<!-- Addresses EDGE-AUTH-001 through EDGE-AUTH-010, EDGE-AUTH-008 -->

### 10.1 Token Expiry Staggering (Addresses EDGE-AUTH-008)

- To prevent thundering herd on token refresh (100K users all expiring simultaneously):
  - Each new access token's `exp` is set to `iat + 900 + random.uniform(-30, 30)` seconds (±30-second jitter).
  - This spreads refresh storms across a 60-second window.

### 10.2 Concurrent Authentication Flows (Addresses EDGE-AUTH-011)

- Two callbacks with the same `state`: the second receives `400 STATE_ALREADY_USED` (see §1.3).
- Duplicate state parameters cannot succeed because the `used_at` field is set atomically.

### 10.3 Concurrent Token Refresh (Addresses EDGE-AUTH-013)

- Concurrent refresh requests from the same session (e.g., multiple browser tabs):
  - The first successful refresh revokes the old token and issues a new one.
  - The second concurrent refresh presents the original token (already revoked) and triggers token theft detection (§3.3).
  - To mitigate false positives from legitimate concurrent refreshes, a **grace period** of 5 seconds is applied: if a refresh token is reused within 5 seconds of its rotation, it is treated as a duplicate request (idempotent) rather than theft.

### 10.4 Session Table Write Contention (Addresses EDGE-AUTH-014)

- All session writes use `SERIALIZABLE` isolation.
- Connection pool overflow (>30 connections) triggers queuing with a 5-second timeout.

---

## §11 — Regulatory Compliance

<!-- Addresses EDGE-AUTH-066, EDGE-AUTH-067, EDGE-AUTH-068, EDGE-AUTH-069 -->

| Framework | Control | OAuth2+MFA Mechanism |
|---|---|---|
| **SOC 2 CC6.1** | Logical access controls | OAuth2 + MFA enforces authenticated access |
| **SOC 2 CC6.6** | Access removal | Logout revokes session; account deactivation blocklists all tokens |
| **SOC 2 CC7.2** | System monitoring | All auth events logged with tamper-evident HMAC |
| **HIPAA §164.312(a)** | Access control | MFA required for all healthcare deployment approvers |
| **HIPAA §164.312(b)** | Audit controls | Auth event log retained 6 years |
| **GDPR Art. 7** | Consent | OAuth2 consent recorded in `oauth_consent_log` |
| **NIST SP 800-63B** | Authentication assurance | TOTP = AAL2; WebAuthn = AAL3 |

---

## §12 — Not In Scope

The following scenarios require external protocol coordination and are tracked as
separate GitHub issues rather than resolved in this spec:

- **Full SIM-swapping mitigation** beyond rate limiting (requires telecom provider coordination).
- **Complete account recovery identity verification protocol** beyond "contact support" (requires organizational process design).
- **Cross-system audit log correlation** beyond `correlation_id` (requires architecture decision for distributed tracing).

See GitHub issues filed by the Spec Edge Case Tester for these gaps.
