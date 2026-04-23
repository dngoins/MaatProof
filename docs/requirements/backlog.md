# Backlog

This file is the queue the BA maintains. The nightly cron reads it and converts pending rows into GitHub Issues.

| Topic Name | Requirements | Tech Stack | Status | Assigned Branch | Notes |
|---|---|---|---|---|---|
| User Authentication | OAuth2 login with MFA support | Python, FastAPI, PostgreSQL | in-progress | main | | High priority — security baseline |
| Audit Logging | Track all state changes with tamper-proof log | Python, SQLite, cryptography | in-progress | main | | Compliance requirement |
| Dashboard API | REST endpoints for proof verification metrics | Python, FastAPI, Redis | in-progress | main | | Needed for monitoring UI |
