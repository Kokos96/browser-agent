
import asyncio
import hmac
import os
import sys
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, HttpUrl

load_dotenv()

API_TOKEN = os.getenv("API_TOKEN", "")
FRONTEND_ORIGIN = os.getenv(
    "FRONTEND_ORIGIN",
    "https://browser-agent-fl9il7rn0-kokos96.vercel.app",
)

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
    allow_origins=[FRONTEND_ORIGIN],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "X-API-Token",
        "ngrok-skip-browser-warning",
    ],
)

process = None
state = "idle"
logs = []
lock = asyncio.Lock()


class RunRequest(BaseModel):
    url: HttpUrl


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


async def collect_output(proc):
    global state

    try:
        while True:
            line = await proc.stdout.readline()

            if not line:
                break

            logs.append(
                line.decode("utf-8", errors="replace").rstrip()
            )
            del logs[:-150]

        code = await proc.wait()
        state = "completed" if code == 0 else "error"
        logs.append(f"Process exited with code {code}")

    except Exception as exc:
        state = "error"
        logs.append(f"Output reader error: {exc}")


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "service": "browser-agent-api",
        "status": state,
    }


@app.post("/api/run")
async def run_agent(
    request: RunRequest,
    x_api_token: str | None = Header(default=None),
):
    global process, state, logs

    authorize(x_api_token)

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
        env = os.environ.copy()
        env["TEST_URL"] = url
        env["HEADLESS"] = "true"

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
        asyncio.create_task(collect_output(process))

    return {
        "status": "running",
        "message": "Agent started",
    }


@app.get("/api/status")
async def get_status(
    x_api_token: str | None = Header(default=None),
):
    authorize(x_api_token)

    return {
        "status": state,
        "logs": logs[-50:],
    }


@app.get("/api/history")
async def get_history(
    x_api_token: str | None = Header(default=None),
):
    import json

    authorize(x_api_token)

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT")

    if project_id:
        try:
            from google.cloud import firestore

            db = firestore.Client(project=project_id)
            docs = (
                db.collection("agent_history")
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

    records.sort(
        key=lambda item: item.get("started_at", "")
        if isinstance(item, dict) else "",
        reverse=True,
    )
    return {"history": records}
