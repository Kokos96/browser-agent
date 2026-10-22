
import asyncio
import hashlib
import hmac
import os
import re
import sys
import uuid
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl

load_dotenv()

API_TOKEN = os.getenv("API_TOKEN", "")
DEFAULT_FRONTEND_ORIGINS = (
    "https://browser-agent-fl9il7rn0-kokos96.vercel.app,"
    "https://browser-agent-web-nine.vercel.app"
)
FRONTEND_ORIGINS = [
    origin.strip().rstrip("/")
    for origin in os.getenv(
        "FRONTEND_ORIGINS",
        os.getenv("FRONTEND_ORIGIN", DEFAULT_FRONTEND_ORIGINS),
    ).split(",")
    if origin.strip()
]
# Keep the current known Vercel deployment reachable even when the legacy
# singular environment variable is still configured in Cloud Run.
CURRENT_FRONTEND_ORIGIN = "https://browser-agent-web-nine.vercel.app"
if CURRENT_FRONTEND_ORIGIN not in FRONTEND_ORIGINS:
    FRONTEND_ORIGINS.append(CURRENT_FRONTEND_ORIGIN)

ALLOWED_HOSTS = {
    host.strip().lower()
    for host in os.getenv(
        "ALLOWED_TEST_HOSTS",
        "quiz-web-wzr7.onrender.com",
    ).split(",")
    if host.strip()
}

app = FastAPI(title="Browser Agent API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "X-API-Token",
        "X-Device-Token",
        "ngrok-skip-browser-warning",
    ],
)

process = None
state = "idle"
state_device_id = None
state_run_key = None
logs = []
lock = asyncio.Lock()


SUPPORTED_GEMINI_MODELS = {
    "gemini-4-argon",
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
}


class RunRequest(BaseModel):
    url: HttpUrl
    first_name: str = ""
    last_name: str = ""
    group_name: str = ""
    model: str | None = None

def device_owner_id(token: str | None) -> str:
    """Return an opaque owner ID derived from a per-browser bearer secret."""
    if not token or not re.fullmatch(r"[0-9a-f]{64}", token):
        raise HTTPException(status_code=400, detail="Invalid or missing device token")
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def authorize(token: str | None):
    if not API_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="API_TOKEN is not configured",
        )

    if not token or not hmac.compare_digest(token, API_TOKEN):
        raise HTTPException(
            status_code=401,
            detail="Invalid API token",
        )


def remove_local_history_records(device_id: str, run_id: str | None = None) -> int:
    """Remove this device's backup records with an atomic file replacement."""
    import json
    import tempfile

    path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "results", "agent_history.json"
    )
    try:
        with open(path, "r", encoding="utf-8") as file:
            records = json.load(file)
    except FileNotFoundError:
        return 0
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail="History file contains invalid JSON") from exc
    if not isinstance(records, list):
        return 0

    kept = [
        item for item in records
        if not (
            isinstance(item, dict)
            and item.get("device_id") == device_id
            and (run_id is None or str(item.get("run_id", "")) == run_id)
        )
    ]
    deleted = len(records) - len(kept)
    if not deleted:
        return 0

    fd, temp_path = tempfile.mkstemp(
        dir=os.path.dirname(path), prefix="history_delete_", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(kept, file, ensure_ascii=False, indent=2, default=str)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
    return deleted


async def collect_output(proc, run_key: str):
    global state, logs

    try:
        while True:
            line = await proc.stdout.readline()

            if not line:
                break

            # A previous reader must never append logs to a newer run.
            if state_run_key == run_key:
                logs.append(line.decode("utf-8", errors="replace").rstrip())
                del logs[:-150]

        code = await proc.wait()
        if state_run_key == run_key:
            state = "completed" if code == 0 else "error"
            logs.append(f"Process exited with code {code}")

    except Exception as exc:
        if state_run_key == run_key:
            state = "error"
            logs.append(f"Output reader error: {exc}")


@app.get("/api/health")
async def health():
    # Public health does not disclose any device's active run status.
    return {"ok": True, "service": "browser-agent-api"}


@app.post("/api/run")
async def run_agent(
    request: RunRequest,
    x_api_token: str | None = Header(default=None),
    x_device_token: str | None = Header(default=None),
):
    global process, state, state_device_id, state_run_key, logs

    authorize(x_api_token)
    device_id = device_owner_id(x_device_token)
    if request.model is not None and request.model not in SUPPORTED_GEMINI_MODELS:
        raise HTTPException(status_code=400, detail="Unsupported Gemini model")

    url = str(request.url)
    parsed = urlparse(url)

    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.hostname.lower() not in ALLOWED_HOSTS
        or parsed.username
        or parsed.password
    ):
        raise HTTPException(
            status_code=400,
            detail="URL is not in the allowed HTTPS hosts",
        )

    async with lock:
        if process is not None and process.returncode is None:
            raise HTTPException(
                status_code=409,
                detail="Agent is already running",
            )

        logs = []
        state_device_id = device_id
        run_key = uuid.uuid4().hex
        state_run_key = run_key
        env = os.environ.copy()
        env["TEST_URL"] = url
        env["USER_NAME"] = request.first_name.strip()
        env["USER_SURNAME"] = request.last_name.strip()
        env["USER_GROUP"] = request.group_name.strip()
        env["HEADLESS"] = "true"
        env["DEVICE_ID"] = device_id
        if request.model:
            env["GEMINI_MODEL"] = request.model

        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "main.py",
                cwd=os.path.dirname(os.path.abspath(__file__)),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except Exception as exc:
            state = "error"
            logs.append(f"Could not start agent: {exc}")
            raise HTTPException(
                status_code=500,
                detail="Could not start the Python agent",
            ) from exc

        state = "running"
        asyncio.create_task(collect_output(process, run_key))

    return {
        "status": "running",
        "message": "Agent started",
    }


@app.get("/api/status")
async def get_status(
    x_api_token: str | None = Header(default=None),
    x_device_token: str | None = Header(default=None),
):
    authorize(x_api_token)
    device_id = device_owner_id(x_device_token)
    if state_device_id != device_id:
        return {"status": "idle", "logs": []}
    return {"status": state, "logs": logs[-50:]}


@app.delete("/api/history/{run_id}")
async def delete_history_item(
    run_id: str,
    x_api_token: str | None = Header(default=None),
    x_device_token: str | None = Header(default=None),
):
    authorize(x_api_token)
    device_id = device_owner_id(x_device_token)
    if process is not None and process.returncode is None and state_device_id == device_id:
        raise HTTPException(status_code=409, detail="Cannot delete history while this device's agent is running")

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT")
    if project_id:
        try:
            from google.cloud import firestore

            ref = (
                firestore.Client(project=project_id)
                .collection("devices").document(device_id)
                .collection("runs").document(run_id)
            )
            if not ref.get().exists:
                raise HTTPException(status_code=404, detail="Run not found")
            ref.delete()
            remove_local_history_records(device_id, run_id)
            return {"deleted": 1}
        except HTTPException:
            raise
        except Exception as exc:
            logs.append(f"Firestore history delete failed: {exc}")
            raise HTTPException(status_code=503, detail="Cloud history delete failed") from exc

    deleted = remove_local_history_records(device_id, run_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"deleted": deleted}


@app.delete("/api/history")
async def delete_all_history(
    x_api_token: str | None = Header(default=None),
    x_device_token: str | None = Header(default=None),
):
    authorize(x_api_token)
    device_id = device_owner_id(x_device_token)
    if process is not None and process.returncode is None and state_device_id == device_id:
        raise HTTPException(status_code=409, detail="Cannot clear history while this device's agent is running")

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT")
    if project_id:
        try:
            from google.cloud import firestore

            db = firestore.Client(project=project_id)
            docs = list(
                db.collection("devices").document(device_id).collection("runs").stream()
            )
            # Firestore batches support at most 500 writes.
            for start in range(0, len(docs), 450):
                batch = db.batch()
                for doc in docs[start:start + 450]:
                    batch.delete(doc.reference)
                batch.commit()
            remove_local_history_records(device_id)
            return {"deleted": len(docs)}
        except HTTPException:
            raise
        except Exception as exc:
            logs.append(f"Firestore history clear failed: {exc}")
            raise HTTPException(status_code=503, detail="Cloud history clear failed") from exc

    deleted = remove_local_history_records(device_id)
    return {"deleted": deleted}


@app.get("/api/history")
async def get_history(
    x_api_token: str | None = Header(default=None),
    x_device_token: str | None = Header(default=None),
):
    import json

    authorize(x_api_token)
    device_id = device_owner_id(x_device_token)

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT")

    if project_id:
        try:
            from google.cloud import firestore

            db = firestore.Client(project=project_id)
            docs = (
                db.collection("devices")
                .document(device_id)
                .collection("runs")
                .stream()
            )
            records = [doc.to_dict() for doc in docs]
            records.sort(
                key=lambda item: item.get("started_at", ""),
                reverse=True,
            )
            return {"history": records}
        except Exception as exc:
            logs.append(f"Firestore history read failed: {exc}")
            raise HTTPException(
                status_code=503,
                detail="Cloud history is temporarily unavailable",
            ) from exc

    history_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "results",
        "agent_history.json",
    )

    try:
        with open(history_path, "r", encoding="utf-8") as file:
            records = json.load(file)
    except FileNotFoundError:
        records = []
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=500,
            detail="History file contains invalid JSON",
        ) from exc

    if not isinstance(records, list):
        records = []

    # Legacy records without an owner stay hidden from all devices.
    records = [
        item for item in records
        if isinstance(item, dict) and item.get("device_id") == device_id
    ]

    records.sort(
        key=lambda item: item.get("started_at", "")
        if isinstance(item, dict) else "",
        reverse=True,
    )
    return {"history": records}
