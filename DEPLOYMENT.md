# Browser Automation Agent — deployment checklist

This change set is intentionally kept on the `feature/device-scoped-history` branch until the backend and frontend can be deployed together.

## What's included

- Per-browser anonymous bearer token; the API derives an opaque owner ID from its SHA-256 hash.
- Firestore storage in `devices/{device_id}/runs/{run_id}`.
- Owner-scoped history read, status/log view, delete-one, and clear-history endpoints.
- Selected Gemini model is validated on the backend, passed through `GEMINI_MODEL`, and stored with the run.
- Result-page URL/title/text excerpt and visible `Ваш результат: N / M` score are saved after automatic submission.
- GitHub Actions syntax checks and backend unit tests.

## Deploy backend first

In Cloud Shell, use the existing checkout. Do not copy or print any secret values.

```bash
cd ~/browser-agent
git status --short
git fetch origin
git switch feature/device-scoped-history 2>/dev/null || git switch -c feature/device-scoped-history --track origin/feature/device-scoped-history
git pull --ff-only origin feature/device-scoped-history
python -m py_compile api_server.py agent.py main.py
python -m unittest discover -s tests -v
```

If `git status --short` shows local edits that you need, do not reset them. In particular, keep any local backup file.

Deploy the existing Cloud Run service from this branch:

```bash
gcloud config set project project-ca4b9b7c-83aa-4b2f-8f8
gcloud run deploy browser-agent --source . --region europe-west1 --project project-ca4b9b7c-83aa-4b2f-8f8
```

This deploy command does not explicitly set or print API keys. Verify the configured secret environment variables remain present in Cloud Run after the rollout; never paste their values into chat or commit them.

Check the public health endpoint:

```bash
curl -fsS https://browser-agent-170941399078.europe-west1.run.app/api/health
```

Expected shape: `{"ok":true,"service":"browser-agent-api"}`.

## Deploy frontend second

Only after the backend deploy succeeds, deploy the frontend branch `feature/device-scoped-history` to a Vercel Preview deployment first. Use the existing Vercel/GitHub integration or the authenticated Vercel CLI in the frontend repository.

```bash
cd ~/browser-agent-web
git status --short
git fetch origin
git switch feature/device-scoped-history 2>/dev/null || git switch -c feature/device-scoped-history --track origin/feature/device-scoped-history
git pull --ff-only origin feature/device-scoped-history
node --check script.js
```

Test the preview URL before promoting it to production. Confirm the following:
1. Device/browser A launches a run and sees only A's history.
2. Device/browser B does not see A's record and sees only B's records.
3. A record survives page refresh.
4. Delete-one and clear-history require confirmation and do not remove another device's records.
5. Model selector affects the next run; the run record shows the actual model and provider.
6. When the submitted page contains `Ваш результат: N / M`, the result page excerpt and score appear in run details.
7. The Home page shows real agent events rather than fabricated percentage progress.

## Demo scope / known limits

- The shared `API_TOKEN` flow remains for this private educational demo. Since the browser sends that token, do not treat it as a production multi-user authentication scheme or publish it broadly.
- The API currently runs one browser agent process at a time. A second simultaneous run receives HTTP 409.
- Legacy documents in the old `agent_history` collection are not deleted or migrated. They remain untouched and are not exposed to newly created device histories.
- Browser identity is tied to the random token in `localStorage`; clearing browser data creates a new empty identity.
- Remaining/available provider quota is not guessed. The UI only totals Gemini token-usage metadata that is present in run records.
