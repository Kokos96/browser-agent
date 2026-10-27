# Deployment checklist and Operational Guide

Do not deploy a working branch to the production Cloud Run service until CI and manual checks pass.

## 1. Validate the main branch locally

~~~bash
git fetch origin
git switch main
git pull --ff-only origin main
python -m pip install -r requirements.txt
python -m py_compile api_server.py main.py src/browser_agent/*.py
python -m unittest discover -s tests -v
~~~

Keep any local edits and backup files. Do not use git reset --hard as a shortcut.

## 2. Review what changed in PR #6

- Process lifecycle watchdog with global execution timeout (`AGENT_RUN_TIMEOUT_SECONDS=180`).
- Background heartbeat loop (`_heartbeat_loop()`) refreshing every 5 seconds.
- Distributed stale run reconciliation (`reconcile_stale_record`) with Firestore and local fallback.
- Multi-instance status routing via `/api/status`.
- Top-level exception interception in `BrowserAgent.run()`, ensuring terminal `error` records are always saved.
- Public HTTPS URL policy by default (`URL_POLICY_MODE=public_https`), with optional exact-host restriction (`URL_POLICY_MODE=strict_allowlist`).
- API-side URL/DNS validation plus a local DNS-pinning egress proxy for Playwright HTTP(S) traffic, including redirects and subresources.
- Autonomous QA & Self-Healing loop framework (`src/browser_agent/autonomous_qa/`).

## 3. Deploy to Cloud Run (Manual Release Step)

> [!IMPORTANT]
> **CPU Allocation Requirement**: `api_server.py` delegates execution to a background subprocess (`asyncio.create_task()`) and immediately returns HTTP 200. Under default Cloud Run request-based billing, CPU is throttled to 0% after the response returns.
> You **MUST** specify `--no-cpu-throttling` (or enable *CPU always allocated* in GCP Console) so the background Playwright process and heartbeats continue running reliably.

Execute the deployment command below from Google Cloud Shell or a workstation with authenticated `gcloud`:

~~~bash
gcloud run deploy browser-agent \
  --source . \
  --region europe-west1 \
  --no-cpu-throttling \
  --update-env-vars=URL_POLICY_MODE=public_https \
  --min-instances 0 \
  --max-instances 1 \
  --timeout 300
~~~

Verify that existing environment variables remain configured in Cloud Run after deployment. `URL_POLICY_MODE=public_https` allows arbitrary syntactically valid public HTTPS hosts after DNS validation; `URL_POLICY_MODE=strict_allowlist` restricts targets to exact hosts in `ALLOWED_TEST_HOSTS`. Keep SSRF protections enabled in both modes. Verify `API_TOKEN` and `GEMINI_API_KEY` remain configured, but never print or paste secret values.

Check public health:

~~~bash
curl -fsS https://browser-agent-170941399078.europe-west1.run.app/api/health
~~~

Expected response shape:

~~~json
{"ok":true,"service":"browser-agent-api"}
~~~

## 4. Rollback Procedure

If the new revision encounters any production issues, revert traffic to the previous known stable revision immediately:

~~~bash
# 1. List revisions to identify the previous revision name
gcloud run revisions list \
  --service browser-agent \
  --region europe-west1

# 2. Redirect 100% of traffic to the target previous revision
gcloud run services update-traffic browser-agent \
  --region europe-west1 \
  --to-revisions PREVIOUS_REVISION_NAME=100
~~~

## 5. Autonomous QA & Self-Healing Loop

The codebase includes an automated audit-fix-validate proof-of-concept loop:

~~~bash
# Run the autonomous QA test suite
python -m unittest tests/test_autonomous_qa.py -v
~~~

The loop operates through 4 distinct decoupled roles:
- **QAAuditor**: Read-only defect scanner and structured report generator.
- **Fixer**: Applies targeted patches in an isolated branch/worktree.
- **IndependentValidator**: Evaluates diffs and test suites with zero developer trust.
- **Orchestrator**: Enforces quality gates, max 5 iterations, and maintains decision logs.

## 6. Required smoke tests

1. Launch a run using the verified test stand: `https://browser-agent-web-nine.vercel.app/test-quiz`.
2. Verify that the agent fills the student profile, answers all 3 questions, and submits.
3. Verify that the score is captured (`Ваш результат: 3 / 3 (100%)`).
4. Verify the API status transitions from `running` to `completed` in UI and polling terminates.
5. Verify an unreachable URL terminates with a controlled `error` or `timeout`.
6. Verify logs and history remain accessible after refreshing the web client.
