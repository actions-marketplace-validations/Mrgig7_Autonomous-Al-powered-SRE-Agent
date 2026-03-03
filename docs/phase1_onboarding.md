# Phase 1 Onboarding Runbook

## OAuth Flow Diagram

```text
Landing Page
  -> POST /api/v1/auth/github/login {action:start}
  -> GitHub OAuth authorize URL (state stored with TTL)
  -> GitHub redirects with code+state
  -> POST /api/v1/auth/github/login {action:exchange, code, state}
  -> Backend validates state + required scopes
  -> JWT access/refresh issued + HttpOnly cookies set
  -> Frontend opens /app dashboard
```

## Repository Install Lifecycle

```text
Dashboard repository selected
  -> POST /api/v1/integration/install
  -> Backend validates repo install permissions (admin/maintain)
  -> Temporary install state saved (TTL)
  -> GitHub App install URL returned
  -> User completes GitHub App install
  -> Callback includes installation_id + state
  -> POST /api/v1/integration/install/confirm
  -> Installation persisted in postgres (github_app_installations)
  -> Onboarding status set to app_installed + dashboard_ready
```

## Required Environment Variables

- `GITHUB_OAUTH_CLIENT_ID`
- `GITHUB_OAUTH_CLIENT_SECRET`
- `GITHUB_OAUTH_REDIRECT_URI`
- `GITHUB_OAUTH_REQUIRED_SCOPES` (default: `repo,read:user,workflow`)
- `GITHUB_APP_INSTALL_URL`
- `JWT_SECRET_KEY`
- `REDIS_URL`
- `DATABASE_URL`
- `PHASE1_ENABLE_DASHBOARD`
- `PHASE1_ENABLE_INSTALL_FLOW`
- `PHASE1_INSTALL_STATE_TTL_SECONDS`
- `PHASE1_ONBOARDING_STATE_TTL_SECONDS`

## Common Failure Cases

- Invalid/expired OAuth state:
  - Symptom: `400 Invalid OAuth state` or `OAuth state expired`
  - Fix: restart login from landing page.
- Missing GitHub scopes:
  - Symptom: `403 Missing required GitHub scopes`
  - Fix: approve requested scopes during GitHub OAuth consent.
- GitHub session expired:
  - Symptom: `401 GitHub session expired` on `/user/repos` or `/integration/install`
  - Fix: re-authenticate with GitHub.
- Missing repo install permission:
  - Symptom: `403 Missing repository permissions for installation`
  - Fix: install as repo/org admin or grant maintain/admin permission.
- Invalid/expired install state:
  - Symptom: `400 Invalid or expired installation state`
  - Fix: regenerate install link from dashboard and retry.
