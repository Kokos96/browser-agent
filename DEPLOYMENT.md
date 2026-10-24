# Deployment checklist

Do not deploy a working branch to the production Cloud Run service until CI and manual checks pass.

## 1. Validate the branch locally

~~~bash
git fetch origin
git switch improve/run-status-and-docs
git pull --ff-only origin improve/run-status-and-docs
python -m pip install -r requirements.txt
python -m py_compile api_server.py main.py src/browser_agent/*.py
python -m unittest discover -s tests -v
~~~

Keep any local edits and backup files. Do not use git reset --hard as a shortcut.

## 2. Review what changed

- Confirm main.py loads modules from src/browser_agent/.
- Confirm page-load and AI-planning failures are saved with status=error.
- Confirm the worker exits non-zero when the agent explicitly reports failure.
- Confirm the API stores the subprocess exit code and synchronizes history status.
- Confirm results/ and screenshots/ remain out of Git and the container build context.

## 3. Deploy only after review

Use the existing Google Cloud project and service configuration. The command below is a manual release step, not something performed by CI:

~~~bash
gcloud run deploy browser-agent \
  --source . \
  --region europe-west1
~~~

Verify that the existing secret-backed environment variables are still configured in Cloud Run after deployment. Never print or paste secret values.

Check public health:

~~~bash
curl -fsS https://browser-agent-170941399078.europe-west1.run.app/api/health
~~~

Expected response shape:

~~~json
{"ok":true,"service":"browser-agent-api"}
~~~

## 4. Required smoke tests

1. Launch a run using a currently available, authorized test URL.
2. Verify that a page-load failure is saved as error, with test_outcome and diagnostic page information.
3. Verify the API status and history agree after the worker exits.
4. Verify a genuinely completed run is not automatically described as a passed test unless a result was captured.
5. Check history isolation from two separate browser/device tokens.
6. Verify logs and history remain available after refreshing the web client.

Deploy the frontend preview only after the backend passes these checks, then test the full flow before promoting the frontend.

## Current limits

- The API currently supports one active worker at a time.
- The shared API_TOKEN is suitable only for the current private demo. Do not treat it as production-grade end-user authentication.
- Firestore records use devices/{device_id}/runs/{run_id} when a Google Cloud project is configured; local JSON is a fallback.
