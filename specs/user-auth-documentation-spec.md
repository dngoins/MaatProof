# User Authentication — Documentation Specification

<!-- Addresses EDGE-001 through EDGE-036 (Issue #121 spec edge case testing) -->

**Parent Issue:** #76 — User Authentication  
**Documentation Issue:** #121 — [User Authentication] Documentation  
**Tech Stack:** Python, FastAPI, PostgreSQL  
**Status:** Spec Phase  

This document defines **what the documentation deliverables for Issue #121 MUST cover**.
It is the spec against which the Documenter Agent and human reviewers validate completeness.

---

## §1 — README Section Requirements

The README section for User Authentication MUST include:

<!-- Addresses EDGE-001, EDGE-002 -->

### 1.1 Feature Overview

- A one-paragraph description of what the User Authentication feature provides.
- Explicitly state which OAuth2 providers are supported (e.g., Google, GitHub, or
  generic OIDC-compliant providers) and any minimum provider requirements
  (PKCE support, OIDC discovery endpoint availability).
- State the MFA method: TOTP-based (RFC 6238), using `pyotp`. List compatible
  authenticator apps (Google Authenticator, Authy, 1Password, Microsoft Authenticator).

### 1.2 Quick-Start Instructions

The quick-start MUST include:

1. **Prerequisites**: Python ≥ 3.11, PostgreSQL ≥ 15, `alembic`, required pip packages.
2. **Environment variables**: A complete table of ALL required environment variables.
   See §6.1 for the mandatory list.
3. **Database setup**: `alembic upgrade head` command to apply migrations.
4. **Running the service**: `uvicorn app.main:app --host 0.0.0.0 --port 8000`
5. **First login**: Brief example of the OAuth2 authorization URL construction.

### 1.3 Links

README section MUST link to:
- Full API reference (`docs/api/user-auth.md` or OpenAPI spec URL)
- MFA setup guide
- Architecture diagram
- Security considerations section

---

## §2 — Architecture Diagram Requirements

<!-- Addresses EDGE-007, EDGE-008, EDGE-013, EDGE-021 -->

The architecture documentation MUST include two Mermaid sequence diagrams:

### 2.1 OAuth2 Authorization Code Flow (with PKCE)

The diagram MUST show:

1. User initiates login → server generates `state` (CSRF nonce, stored in session)
   and `code_verifier`/`code_challenge` (PKCE, S256 method).
2. Browser redirects to OAuth2 provider with `response_type=code`, `client_id`,
   `redirect_uri`, `scope`, `state`, `code_challenge`, `code_challenge_method=S256`.
3. Provider authenticates user and redirects to `/auth/callback?code=…&state=…`.
4. Server validates `state` matches session value (CSRF prevention).
5. Server exchanges `code` + `code_verifier` for tokens via provider's token endpoint.
6. Server validates the ID token (signature, `iss`, `aud`, `exp` claims).
7. Server upserts user record in PostgreSQL; issues JWT access token + refresh token.
8. If MFA is enabled for user: redirect to `/auth/mfa/verify` before issuing tokens.

```mermaid
sequenceDiagram
    participant U as User Browser
    participant S as FastAPI Server
    participant P as OAuth2 Provider
    participant DB as PostgreSQL

    U->>S: GET /auth/login?provider=google
    S->>S: Generate state (CSRF nonce) + code_verifier/challenge (PKCE)
    S->>S: Store state in server-side session (TTL 10 min)
    S-->>U: 302 Redirect → Provider /authorize?...&state=X&code_challenge=Y
    U->>P: Browser follows redirect
    P->>P: Authenticate user
    P-->>U: 302 Redirect → /auth/callback?code=Z&state=X
    U->>S: GET /auth/callback?code=Z&state=X
    S->>S: Validate state == session state (CSRF check)
    S->>P: POST /token (code + code_verifier)
    P-->>S: access_token, id_token, refresh_token
    S->>S: Validate id_token signature + claims
    S->>DB: UPSERT users (oauth_sub, email, provider)
    alt MFA enabled
        S-->>U: 302 /auth/mfa/verify (session: pending_user_id)
    else MFA not enabled
        S->>S: Issue JWT access_token + refresh_token
        S-->>U: 200 {access_token, refresh_token, expires_in}
    end
```

### 2.2 MFA Verification Sequence

The diagram MUST show the full TOTP verification flow including the time-window
tolerance and backup code fallback:

```mermaid
sequenceDiagram
    participant U as User
    participant S as FastAPI Server
    participant DB as PostgreSQL

    Note over U,S: User has pending_user_id in server session after OAuth2

    alt Enroll new MFA device
        U->>S: POST /auth/mfa/enroll
        S->>S: Generate TOTP secret (pyotp.random_base32())
        S->>S: Generate 10 backup codes (cryptographically random)
        S->>DB: Store encrypted TOTP secret + hashed backup codes
        S-->>U: 200 {otpauth_uri, qr_code_svg, backup_codes}
        Note over U: User scans QR code with authenticator app
        U->>S: POST /auth/mfa/enroll/confirm {totp_code}
        S->>S: Verify TOTP (±1 window = ±30s clock drift tolerance)
        S->>DB: Set mfa_enabled=true, mfa_confirmed_at=now()
        S-->>U: 200 {enrolled: true}
    else Verify existing MFA
        U->>S: POST /auth/mfa/verify {totp_code OR backup_code}
        alt TOTP code path
            S->>S: Verify TOTP (±1 window tolerance for clock drift)
            S->>DB: Check code not in used_totp_windows (replay prevention)
        else Backup code path
            S->>DB: Check hashed backup_code exists + not_used
            S->>DB: Mark backup_code as used
            Note over S: Warn user N backup codes remaining
        end
        S->>S: Issue JWT access_token + refresh_token
        S-->>U: 200 {access_token, refresh_token, expires_in}
    end
```

### 2.3 Notes on Diagrams

The architecture documentation MUST include prose notes explaining:
- Why PKCE is required (even for confidential clients, as defense-in-depth).
- Why `state` is a server-side nonce (not client-side) — prevents open redirect attacks.
- TOTP ±1 window (30-second window ± 1 = ±30 seconds tolerance for clock drift).

---

## §3 — API Reference Requirements

<!-- Addresses EDGE-003, EDGE-004, EDGE-022, EDGE-030 -->

The API reference MUST document all endpoints listed in Issue #96, including:

### 3.1 Endpoints Table

| Endpoint | Method | Auth Required | Description |
|---|---|---|---|
| `/auth/login` | GET | No | Initiate OAuth2 flow; redirects to provider |
| `/auth/callback` | GET | No | OAuth2 callback handler |
| `/auth/token` | POST | No | Issue tokens (used by confidential clients) |
| `/auth/token/refresh` | POST | Refresh token | Rotate access + refresh token |
| `/auth/logout` | POST | Access token | Revoke tokens + clear session |
| `/auth/mfa/enroll` | POST | Partial (pending session) | Begin MFA enrollment |
| `/auth/mfa/enroll/confirm` | POST | Partial (pending session) | Confirm MFA enrollment |
| `/auth/mfa/verify` | POST | Partial (pending session) | Verify TOTP or backup code |
| `/auth/mfa/backup-codes/regenerate` | POST | Access token + MFA | Regenerate backup codes |
| `/auth/me` | GET | Access token | Get current user profile |

### 3.2 JWT Claims Structure

<!-- Addresses EDGE-030 -->

The documentation MUST specify the full JWT payload structure:

```json
{
  "sub": "usr_a3f8b2c1d4e5",
  "email": "user@example.com",
  "provider": "google",
  "scope": ["read:profile", "deploy:staging"],
  "mfa_verified": true,
  "iat": 1700000000,
  "exp": 1700003600,
  "jti": "550e8400-e29b-41d4-a716-446655440000",
  "iss": "https://api.maatproof.dev",
  "aud": "maatproof-api"
}
```

**Field documentation requirements:**
- `sub`: User ID format (`usr_<hex>`) — NOT the OAuth2 `sub` claim (provider-specific)
- `jti`: UUID v4 per token; stored in Redis/DB for revocation checking
- `mfa_verified`: Boolean; tokens issued before MFA verification have `mfa_verified: false`
- `exp`: Access tokens expire in 3600 seconds (1 hour); refresh tokens expire in 2592000 seconds (30 days)
- `scope`: Array of permission strings; starts as read-only, elevated by deployment policy

### 3.3 Token Refresh Policy

<!-- Addresses EDGE-003 -->

Documentation MUST specify:
- **Refresh token rotation**: each use of a refresh token issues a new refresh token and
  invalidates the old one (single-use refresh tokens).
- **Refresh token family detection**: if a previously-used (invalidated) refresh token is
  presented, ALL tokens in the family are immediately revoked (token theft detection).
- **Refresh token binding**: refresh tokens are bound to the user's device fingerprint
  (User-Agent + IP hash); refresh from a different device requires re-authentication.

### 3.4 Token Revocation

<!-- Addresses EDGE-010 -->

Documentation MUST specify:
- `POST /auth/logout` revokes BOTH the access token and refresh token via the `jti` claim.
- Revoked `jti` values are stored in a blocklist (Redis with TTL = token `exp`).
- The API validates `jti` on every authenticated request; revoked tokens are rejected
  with `401 TOKEN_REVOKED`.

### 3.5 Request/Response Schemas

Each endpoint documentation MUST include:
- Full JSON request body schema (field names, types, required/optional, constraints)
- Full JSON response body schema for success (2xx)
- All possible error responses

### 3.6 Error Codes

<!-- Addresses EDGE-004 -->

The documentation MUST include a complete error code table:

| Code | HTTP Status | Description |
|---|---|---|
| `INVALID_OAUTH_STATE` | 400 | OAuth2 `state` parameter mismatch (CSRF detected) |
| `INVALID_OAUTH_CODE` | 400 | Authorization code invalid or expired |
| `PROVIDER_UNAVAILABLE` | 503 | OAuth2 provider endpoint unreachable |
| `INVALID_TOKEN` | 401 | JWT signature invalid or malformed |
| `TOKEN_EXPIRED` | 401 | JWT `exp` claim in the past |
| `TOKEN_REVOKED` | 401 | `jti` found in revocation blocklist |
| `MFA_REQUIRED` | 403 | Token issued without MFA; endpoint requires `mfa_verified: true` |
| `MFA_INVALID_CODE` | 400 | TOTP code incorrect or window expired |
| `MFA_CODE_ALREADY_USED` | 400 | TOTP code from same time window reused (replay prevention) |
| `MFA_BACKUP_CODE_INVALID` | 400 | Backup code not found or already used |
| `MFA_BACKUP_CODES_EXHAUSTED` | 400 | All 10 backup codes have been used; regeneration required |
| `RATE_LIMIT_EXCEEDED` | 429 | Too many requests; see `Retry-After` header |
| `USER_NOT_FOUND` | 404 | User account not found |
| `ACCOUNT_LOCKED` | 423 | Account locked after N failed attempts; see `Retry-After` |

### 3.7 Rate Limits

<!-- Addresses EDGE-022 -->

Documentation MUST specify per-endpoint rate limits:

| Endpoint | Rate Limit | Window | Notes |
|---|---|---|---|
| `GET /auth/login` | 20 req | 1 min | Per IP |
| `POST /auth/token` | 10 req | 1 min | Per IP |
| `POST /auth/mfa/verify` | 5 req | 1 min | Per user; 3 failures → 5-min lockout |
| `POST /auth/token/refresh` | 30 req | 1 min | Per user |
| `POST /auth/logout` | 10 req | 1 min | Per user |

Rate limit headers returned on all responses:
```
X-RateLimit-Limit: 5
X-RateLimit-Remaining: 4
X-RateLimit-Reset: 1700001000
Retry-After: 60   (only on 429)
```

---

## §4 — MFA Setup Guide Requirements

<!-- Addresses EDGE-005, EDGE-006, EDGE-015, EDGE-018, EDGE-033 -->

The MFA setup guide for end users MUST include:

### 4.1 TOTP App Enrollment

Step-by-step instructions:

1. Navigate to Account Settings → Security → Enable Two-Factor Authentication.
2. Open your authenticator app (Google Authenticator, Authy, 1Password, Microsoft Authenticator,
   or any RFC 6238-compliant app).
3. Tap "+" or "Add account"; scan the QR code shown on screen.
4. Enter the 6-digit code shown in your authenticator app to confirm enrollment.
5. **Save your backup codes** (shown once at enrollment; store securely).

### 4.2 Clock Drift Handling

<!-- Addresses EDGE-015 -->

The guide MUST include a troubleshooting section:

> **"My TOTP code isn't working"**
> 
> The most common cause is device clock drift. The server accepts codes from the
> current 30-second window ±1 window (±30 seconds total tolerance). If your device
> clock is more than 30 seconds off:
> 1. Sync your device clock: Settings → General → Date & Time → Set Automatically.
> 2. On Android: Settings → Date & Time → Automatic date & time.
> 3. If time sync is not available, your backup codes will still work.

### 4.3 Backup Codes

<!-- Addresses EDGE-006, EDGE-018 -->

The guide MUST cover:

1. **At enrollment**: 10 single-use backup codes are shown once. Copy and store them
   in a password manager or print and lock in a safe.
2. **Using a backup code**: At the MFA prompt, click "Use backup code"; enter any
   unused backup code. **Each code is invalidated after first use.**
3. **Monitoring usage**: Your account settings show how many backup codes remain.
   A warning is shown when fewer than 3 codes remain.
4. **Regenerating backup codes**:
   - Navigate to Account Settings → Security → Regenerate Backup Codes.
   - This requires TOTP verification (or an existing backup code).
   - **Regeneration invalidates ALL previously issued backup codes immediately.**
5. **If ALL backup codes are exhausted and TOTP device is unavailable**:
   - Contact your administrator or use the account recovery path (see §4.4).

### 4.4 MFA Recovery (Lost Device)

<!-- Addresses EDGE-006 critical gap -->

The guide MUST document the recovery path when a user cannot access their TOTP device
AND has no remaining backup codes:

1. User contacts administrator or opens a support ticket.
2. Administrator verifies user identity through an out-of-band process (email confirmation
   + video call, or organization-specific procedure).
3. Administrator calls `POST /admin/users/{user_id}/mfa/reset` with admin credentials.
4. User re-enrolls MFA on their new device.
5. The MFA reset event is recorded in the audit log with the administrator's identity.

> **Note**: The admin-initiated MFA reset endpoint (`POST /admin/users/{user_id}/mfa/reset`)
> is an administrative API. It MUST require admin-level JWT scope (`admin:users`) and
> MUST log the reset action with the admin's `sub`, timestamp, and reason in the audit log
> per CONSTITUTION §7.

### 4.5 Disabling MFA

The guide MUST note: MFA can only be disabled by an administrator for accounts in
organizations that require it. Personal accounts may disable MFA from Account Settings
→ Security → Disable Two-Factor Authentication (requires current TOTP code).

---

## §5 — Security Considerations Documentation

<!-- Addresses EDGE-007, EDGE-008, EDGE-009, EDGE-011, EDGE-012, EDGE-013, EDGE-027 -->

A security considerations section MUST be included in the documentation, covering:

### 5.1 CSRF and OAuth2 State Parameter

<!-- Addresses EDGE-007, EDGE-013 -->

Documentation MUST explain:
- The `state` parameter is a cryptographically random nonce (32 bytes, base64url-encoded).
- It is stored server-side (not in a cookie that could be manipulated) and validated
  on callback. A mismatch returns `INVALID_OAUTH_STATE (400)`.
- This prevents Cross-Site Request Forgery (CSRF) attacks where an attacker tricks a
  user's browser into completing an OAuth2 flow.

### 5.2 PKCE (Proof Key for Code Exchange)

<!-- Addresses EDGE-008 -->

Documentation MUST explain:
- All OAuth2 flows use PKCE with `code_challenge_method=S256` (SHA-256).
- This prevents authorization code interception attacks.
- Providers MUST support PKCE. If a configured provider does not support PKCE,
  the server will raise `PROVIDER_CONFIG_ERROR` and refuse to initiate the flow.

### 5.3 Token Storage Guidelines (for API Client Developers)

<!-- Addresses EDGE-009 -->

Documentation for API consumers MUST include:
- **Do not store access tokens in `localStorage`** — vulnerable to XSS attacks.
- Store access tokens in memory (JavaScript); use `httpOnly` cookies for refresh tokens.
- For mobile apps: use platform secure storage (iOS Keychain, Android Keystore).
- For server-to-server: use environment variables or secrets managers (not code).

### 5.4 Account Lockout Policy

<!-- Addresses EDGE-011 -->

Documentation MUST specify:
- After **5 consecutive failed MFA verification attempts** within 1 minute, the account
  MFA verification is locked for 5 minutes (not the whole account, to prevent DoS).
- After **10 consecutive failed login attempts** (wrong password or OAuth2 errors)
  within 10 minutes, the account is locked for 30 minutes.
- Account lockout is logged in the audit trail with the triggering IP address.
- Administrators can manually unlock accounts via `POST /admin/users/{user_id}/unlock`.

### 5.5 JWT Algorithm Enforcement

<!-- Addresses EDGE-012 -->

Documentation MUST state:
- Only `RS256` (RSA + SHA-256) or `ES256` (ECDSA + SHA-256) are accepted.
- The `alg: none` attack is explicitly blocked — any JWT with `alg: none` or
  `alg: HS256` (where a symmetric key could be the public key) is rejected with
  `INVALID_TOKEN (401)`.
- JWT signing keys are RSA-4096 or P-256 ECDSA; stored in the configured KMS
  (see §6.4).

### 5.6 Backup Code Security

<!-- Addresses EDGE-027 -->

Documentation MUST specify (for operators):
- Backup codes are stored in PostgreSQL as `bcrypt` hashes (cost factor 12),
  NOT in plaintext.
- Codes are generated as 8-character alphanumeric strings (cryptographically random).
- The plaintext codes are shown to the user exactly once at enrollment; they are
  not retrievable after that.

---

## §6 — Deployment Notes Requirements

<!-- Addresses EDGE-002, EDGE-034, EDGE-035, EDGE-036 -->

### 6.1 Required Environment Variables

<!-- Addresses EDGE-002 -->

The deployment notes MUST include a complete table of environment variables:

| Variable | Required | Example | Description |
|---|---|---|---|
| `DATABASE_URL` | ✅ | `postgresql://user:pass@host:5432/authdb` | PostgreSQL connection string |
| `JWT_PRIVATE_KEY_PATH` | ✅ | `/run/secrets/jwt_private.pem` | Path to RSA-4096 or P-256 private key PEM |
| `JWT_PUBLIC_KEY_PATH` | ✅ | `/run/secrets/jwt_public.pem` | Path to corresponding public key PEM |
| `JWT_ACCESS_TOKEN_EXPIRE_SECONDS` | ✅ | `3600` | Access token TTL (default: 3600) |
| `JWT_REFRESH_TOKEN_EXPIRE_SECONDS` | ✅ | `2592000` | Refresh token TTL (default: 30 days) |
| `OAUTH2_PROVIDER` | ✅ | `google` | OAuth2 provider identifier |
| `OAUTH2_CLIENT_ID` | ✅ | `1234-abc.apps.googleusercontent.com` | OAuth2 client ID |
| `OAUTH2_CLIENT_SECRET` | ✅ | (secret) | OAuth2 client secret |
| `OAUTH2_REDIRECT_URI` | ✅ | `https://api.example.com/auth/callback` | Registered callback URL |
| `OAUTH2_SCOPES` | ✅ | `openid email profile` | Space-separated OAuth2 scopes |
| `SESSION_SECRET_KEY` | ✅ | (min 32-byte random) | Key for server-side session signing |
| `REDIS_URL` | ✅ | `redis://localhost:6379/0` | Redis for token revocation blocklist + sessions |
| `TOTP_ISSUER_NAME` | ✅ | `MaatProof` | Shown in authenticator app |
| `ALLOWED_ORIGINS` | ✅ | `https://app.maatproof.dev` | CORS allowed origins (comma-separated) |
| `RATE_LIMIT_BACKEND` | No | `redis` | Rate limiter backend (`redis` or `memory`) |
| `LOG_LEVEL` | No | `INFO` | Logging level |

### 6.2 PostgreSQL Requirements

<!-- Addresses EDGE-034 -->

- **Minimum version**: PostgreSQL 15 (for `gen_random_uuid()` and `pgcrypto`).
- **Required extensions**: `pgcrypto` (for UUID generation if not using Python-side).
- **Migrations**: `alembic upgrade head` must be run before starting the service.
- **Connection pooling**: Use `asyncpg` with a pool size of 5–20 connections.
  SQLAlchemy `pool_pre_ping=True` to handle stale connections.

### 6.3 Health Check Endpoint

<!-- Addresses EDGE-035 -->

The service MUST expose:

```
GET /health
```

Response (200 OK):
```json
{
  "status": "healthy",
  "database": "connected",
  "redis": "connected",
  "version": "1.0.0"
}
```

Response (503 Service Unavailable) when any dependency is unhealthy:
```json
{
  "status": "unhealthy",
  "database": "disconnected",
  "redis": "connected",
  "version": "1.0.0"
}
```

This endpoint is unauthenticated and MUST be available for liveness/readiness probes.

### 6.4 JWT Key Rotation in Production

<!-- Addresses EDGE-036 -->

> **Complex operational procedure** — see GitHub issue filed for full runbook.
> Summary: JWT keys use a key ID (`kid`) header claim. The service supports multiple
> active public keys simultaneously to allow seamless rotation:
> 1. Generate new key pair and deploy with new `kid`.
> 2. Update JWKS endpoint to serve both old and new public keys.
> 3. New tokens are signed with new key; old tokens remain verifiable via old public key.
> 4. After `JWT_ACCESS_TOKEN_EXPIRE_SECONDS` passes, remove old public key from JWKS.

### 6.5 CORS Configuration

<!-- Addresses EDGE-019 -->

Documentation MUST specify:
- CORS is configured via `ALLOWED_ORIGINS` environment variable.
- The auth endpoints expose `Access-Control-Allow-Origin` only for listed origins.
- Credentials (`withCredentials: true`) are supported for `httpOnly` cookie flows.
- The `/health` endpoint is exempt from CORS restrictions.

---

## §7 — Compliance and Audit Logging Requirements

<!-- Addresses EDGE-023, EDGE-024, EDGE-025 -->

### 7.1 Auth Events in Audit Log

<!-- Addresses EDGE-024, CONSTITUTION §7 -->

Per CONSTITUTION §7, the following authentication events MUST be recorded in the
audit log (`AuditEntry`) with HMAC-SHA256 signatures:

| Event | Logged Fields |
|---|---|
| `USER_LOGIN_SUCCESS` | `user_id`, `provider`, `ip_address`, `user_agent`, `mfa_used` |
| `USER_LOGIN_FAILURE` | `attempted_email`, `provider`, `ip_address`, `reason` |
| `MFA_ENROLL` | `user_id`, `ip_address`, `enrolled_at` |
| `MFA_VERIFY_SUCCESS` | `user_id`, `ip_address`, `method` (totp or backup_code) |
| `MFA_VERIFY_FAILURE` | `user_id`, `ip_address`, `attempt_count` |
| `MFA_RESET` | `user_id`, `admin_id`, `reason`, `ip_address` |
| `ACCOUNT_LOCKED` | `user_id`, `ip_address`, `failure_count`, `lock_expires_at` |
| `TOKEN_REVOKED` | `user_id`, `jti`, `reason` (logout/rotation/admin) |
| `BACKUP_CODES_REGENERATED` | `user_id`, `admin_id` (if admin-initiated) |

### 7.2 GDPR and Data Privacy

<!-- Addresses EDGE-023 -->

The documentation MUST include a note:

> **Data Retention**: User authentication records (login history, MFA enrollment) are
> retained per the organization's data retention policy. The default retention period
> for authentication audit logs is 90 days (configurable, see `specs/audit-logging-spec.md`
> §Retention Policy).
>
> **Right to Erasure (GDPR Article 17)**: A user deletion request (`DELETE /admin/users/{id}`)
> anonymizes authentication records (replaces PII with `[DELETED]`) rather than hard-deleting
> them, to preserve audit trail integrity. The HMAC chain remains intact; only identifying
> fields are zeroed.
>
> **Note**: Anonymization of audit records requires a governance vote per
> `specs/audit-logging-spec.md` §GDPR Compliance, as it modifies immutable audit data.

### 7.3 Password Policy

<!-- Addresses EDGE-025 (NIST guidelines) -->

Documentation MUST specify password policy per NIST SP 800-63B:
- **Minimum length**: 12 characters (NIST recommends ≥8; we enforce ≥12 for defense-in-depth).
- **No complexity rules** (NIST explicitly discourages forced complexity characters).
- **Breach checking**: Passwords are checked against the HaveIBeenPwned API
  (k-anonymity model — only first 5 chars of SHA-1 hash are sent).
- **No expiry** (NIST discourages periodic forced rotation unless compromise suspected).
- **Hashing**: bcrypt with cost factor 12.

---

## §8 — Multi-Tenant Considerations

<!-- Addresses EDGE-021 -->

> **Note**: Full multi-tenant isolation spec is filed as a GitHub issue for architectural
> decision. The documentation MUST include a note:
>
> The User Authentication service enforces tenant isolation via the `tenant_id` claim
> in the JWT. All database queries include a `WHERE tenant_id = :tenant_id` filter.
> Cross-tenant token usage returns `403 FORBIDDEN`. See the multi-tenant architecture
> ADR (to be filed) for full isolation guarantees.

---

## §9 — OAuth2 Provider Availability

<!-- Addresses EDGE-014 -->

The deployment notes MUST include a section on provider availability:

> If the configured OAuth2 provider is unreachable (network error, provider outage),
> the server returns `503 PROVIDER_UNAVAILABLE`. The service MUST implement an
> exponential backoff retry (3 attempts, max 5 seconds total) before returning 503.
> Operators should configure a health alert on `PROVIDER_UNAVAILABLE` log events.
> There is no automatic fallback to a secondary provider in the current implementation.

---

## §10 — Spec Coverage Checklist

This checklist maps the edge case scenarios (EDGE-001 to EDGE-036) to spec sections:

| Scenario | Severity | Addressed By |
|---|---|---|
| EDGE-001: Supported OAuth2 providers not documented | High | §1.1 |
| EDGE-002: Environment variables not listed | Critical | §6.1 |
| EDGE-003: Token refresh rotation policy not documented | High | §3.3 |
| EDGE-004: Rate limit error code (429) missing | Medium | §3.6, §3.7 |
| EDGE-005: TOTP wrong code troubleshooting | Medium | §4.2 |
| EDGE-006: Backup code recovery (lost device) | Critical | §4.3, §4.4 |
| EDGE-007: OAuth2 CSRF/state parameter | Critical | §2.1, §5.1 |
| EDGE-008: PKCE not documented | Critical | §2.1, §5.2 |
| EDGE-009: Token storage security for clients | High | §5.3 |
| EDGE-010: Token revocation not documented | High | §3.4 |
| EDGE-011: Account lockout policy | High | §5.4 |
| EDGE-012: JWT alg:none attack | Critical | §5.5 |
| EDGE-013: Session fixation during OAuth2 | High | §2.1, §5.1 |
| EDGE-014: OAuth2 provider outage handling | High | §9 |
| EDGE-015: TOTP clock drift tolerance | Medium | §4.2 |
| EDGE-016: DB failure during token issuance | Medium | Filed as issue (complex) |
| EDGE-017: JWT key rotation during active sessions | High | §6.4 |
| EDGE-018: Backup codes exhausted | High | §4.3 |
| EDGE-019: CORS not documented | High | §6.5 |
| EDGE-020: Webhook delivery for auth events | Low | N/A (out of scope) |
| EDGE-021: Multi-tenant isolation | High | §8 |
| EDGE-022: Rate limiting thresholds | High | §3.7 |
| EDGE-023: GDPR data deletion | High | §7.2 |
| EDGE-024: Auth audit log requirements | Critical | §7.1 |
| EDGE-025: Password complexity (NIST) | Medium | §7.3 |
| EDGE-026: OAuth2 token binding | Medium | Filed as issue (complex) |
| EDGE-027: Backup code hashing | High | §5.6 |
| EDGE-028: OAuth2 state server-side storage | Critical | §2.1, §5.1 |
| EDGE-029: Concurrent MFA enrollment | High | Filed as issue (complex) |
| EDGE-030: JWT claims structure | High | §3.2 |
| EDGE-031: Unicode usernames in OAuth2 | Low | Filed as issue |
| EDGE-032: Long authorization code validation | Low | Filed as issue |
| EDGE-033: TOTP vs HOTP compatibility | Medium | §4.1 |
| EDGE-034: PostgreSQL version requirements | Medium | §6.2 |
| EDGE-035: Health check endpoint | High | §6.3 |
| EDGE-036: JWT key rotation runbook | High | §6.4 |
