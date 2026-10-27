# Deployment checklist and Operational Guide

Do not deploy a working branch to the production Cloud Run service until CI and manual checks pass.

## 1. Validate the branch locally

~~~
git fetch origin
git switch feat/public-https-url-policy
git pull --ff-only origin feat/public-https-url-policy
python -m pip install -r requirements.txt
python -m py_compile api_server.py main.py public_url_security.py src/browser_agent/*.py
python -m unittest discover -s tests -v
~~~

Keep any local edits and backup files. Do not use `git reset --hard` as a shortcut.

## 2. URL policy change

This release changes the default URL policy from an exact hostname allowlist to `URL_POLICY_MODE=public_https`.

- Any syntactically valid public HTTPS target may be submitted without adding each domain to `ALLOWED_TEST_HOSTS`.
- The API rejects credentials in URLs, non-HTTPS initial targets, unusual ports, local/intranet hostnames, and private/reserved/metadata IP destinations.
- Chromium is configured to send HTTP(S) traffic through a loopback-only egress proxy. The proxy validates each requested hostname's DNS answers and connects to the validated IP address, preventing a second DNS lookup from redirecting that connection to a private IP.
- `URL_POLICY_MODE=strict_allowlist` explicitly restores exact-host matching via `ALLOWED_TEST_HOSTS`, while retaining the same URL and egress security checks.
- The proxy and URL validation are application-layer protection, not a substitute for network egress controls when those are available.

Review the diff carefully, with particular attention to DNS resolution, HTTP CONNECT handling, loopback proxy bypass behavior, and browser schemes.

## 3. Deploy to Cloud Run (Manual Release Step)

> [!IMPORTANT]
> `api_server.py` delegates execution to a background subprocess and immediately returns an HTTP response. Use `--no-cpu-throttling` (or equivalent CPU-always-allocated configuration) so the Playwright worker and heartbeat loop can continue running.

Before deploying, ensure existing secret-backed environment variables (`API_TOKEN`, `GEMINI_API_KEY`) remain configured in Cloud Run. Check whether `URL_POLICY_MODE` is explicitly set; for general public test support set it to `public_https`. Do not print or paste secret values.

From the merged `main` branch, deploy with the existing service settings:

~~~
gcloud run deploy browser-agent \
  --source . \
  --region europe-west1 \
  --no-cpu-throttling \
  --min-instances 1 \
  --max-instances 1 \
  --timeout 300
~~~

This service configuration keeps one warm instance and can incur idle costs. Do not change instance limits or billing mode without considering the demo reliability/cost trade-off.

Verify the active revision and public health:

~~~
gcloud run services describe browser-agent \
  --region europe-west1 \
  --format='yaml(status.url,status.latestReadyRevisionName,status.traffic,spec.template.metadata.annotations,spec.template.spec.timeoutSeconds)'

curl -fsS https://browser-agent-170941399078.europe-west1.run.app/api/health
~~~

Expected health response:

~~~
{"ok":true,"service":"browser-agent-api"}
~~~

## 4. Required URL security tests

1. Confirm the controlled test stand is accepted under `public_https`.
2. Confirm a public HTTPS host such as `www.giggleus.com` is not rejected merely because it is absent from `ALLOWED_TEST_HOSTS`.
3. Confirm exact-host matching is enforced only in `strict_allowlist`.
4. Confirm credentials, non-HTTPS initial URLs, invalid ports, localhost, private IPv4/IPv6, link-local, reserved and metadata destinations are rejected.
5. Confirm DNS answers containing any unsafe address are rejected.
6. Confirm Chromium uses the egress proxy and requests to non-HTTP(S) schemes are blocked.
7. Confirm the proxy rejects CONNECT to loopback/private addresses and pins connections to validated public addresses.
8. Confirm all existing lifecycle, timeout, history, device-isolation and regression tests pass.

## 5. Production smoke tests

1. Launch a run using the verified controlled stand: `https://browser-agent-web-nine.vercel.app/test-quiz`.
2. Verify the agent opens the page, detects form fields and question controls, and submits according to `AUTO_SUBMIT`.
3. Verify the actual score is captured only when visible in result-page content.
4. Verify status transitions from `running` to a terminal state and polling stops.
5. Submit `https://www.giggleus.com/ru/quizzes/science` and verify the old generic allowlist error no longer occurs.
6. Check the Giggleus page actually loads and whether the agent can identify the questions. A CAPTCHA, access restriction or unsupported page structure must be reported explicitly, not represented as a successful pass.
7. Verify logs and history remain accessible after refreshing the web client.
8. Verify a private/local target and a redirect to a private target are blocked.

## 6. Rollback Procedure

If the new revision encounters production issues, redirect traffic to the previous known stable revision:

~~~
gcloud run revisions list --service browser-agent --region europe-west1

gcloud run services update-traffic browser-agent \
  --region europe-west1 \
  --to-revisions PREVIOUS_REVISION_NAME=100
~~~

## 7. Autonomous QA & Self-Healing Loop

The repository contains an audit/fix/validate proof of concept. Keep it separate from production URL-security changes and run its tests independently:

~~~
python -m unittest tests/test_autonomous_qa.py -v
~~~

The loop has four roles:
- **QAAuditor**: read-only defect scanner and structured report generator.
- **Fixer**: applies a targeted patch in an isolated branch/worktree.
- **IndependentValidator**: checks the diff and regression suite.
- **Orchestrator**: orders work by priority, caps iterations, and maintains decision logs.
