
import asyncio
from datetime import datetime, timezone
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

DEFAULT_ALLOWED_TEST_HOSTS = (
    "quiz-web-wzr7.onrender.com,"
    "browser-agent-web-nine.vercel.app"
)
ALLOWED_HOSTS = {
    host.strip().lower()
    for host in os.getenv(
        "ALLOWED_TEST_HOSTS",
        DEFAULT_ALLOWED_TEST_HOSTS,
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

DEFAULT_AGENT_RUN_TIMEOUT_SECONDS = 180
AGENT_RUN_TIMEOUT_SECONDS = int(
    os.getenv("AGENT_RUN_TIMEOUT_SECONDS", str(DEFAULT_AGENT_RUN_TIMEOUT_SECONDS))
)
HEARTBEAT_INTERVAL_SECONDS = 5
STALE_HEARTBEAT_THRESHOLD_SECONDS = int(
    os.getenv("STALE_HEARTBEAT_THRESHOLD_SECONDS", "30")
)

process = None
state = "idle"
state_device_id = None
state_run_key = None
state_started_at = None
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


def record_run_start(
    device_id: str,
    run_id: str,
    url: str,
    model: str | None = None,
):
    """Persist the initial 'running' state immediately to history and Firestore."""
    import json
    import tempfile

    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    initial_record = {
        "device_id": device_id,
        "run_id": run_id,
        "model": model or "gemini-3.6-flash",
        "model_provider": "Google Gemini",
        "started_at": started_at,
        "last_heartbeat_at": started_at,
        "finished_at": None,
        "duration_seconds": None,
        "status": "running",
        "test_outcome": "pending",
        "score": None,
        "url": url,
        "actions": [],
        "logs": [],
        "log_count": 0,
    }

    history_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "results",
        "agent_history.json",
    )
    os.makedirs(os.path.dirname(history_path), exist_ok=True)

    try:
        try:
            with open(history_path, "r", encoding="utf-8") as file:
                records = json.load(file)
            if not isinstance(records, list):
                records = []
        except (FileNotFoundError, json.JSONDecodeError):
            records = []

        records.append(initial_record)

        fd, temp_path = tempfile.mkstemp(
            dir=os.path.dirname(history_path), prefix="history_start_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(records, file, ensure_ascii=False, indent=2, default=str)
            os.replace(temp_path, history_path)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
    except Exception as exc:
        print(f"Local start record save failed: {exc}")

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT")
    if project_id:
        try:
            from google.cloud import firestore

            ref = (
                firestore.Client(project=project_id)
                .collection("devices")
                .document(device_id)
                .collection("runs")
                .document(str(run_id))
            )
            ref.set(initial_record, merge=True)
        except Exception as exc:
            print(f"Firestore start record save failed: {exc}")


def update_run_heartbeat(
    device_id: str,
    run_id: str,
    latest_logs: list[dict] | None = None,
):
    """Update last_heartbeat_at and optional latest logs in Firestore and local history."""
    import json
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT")
    if project_id:
        try:
            from google.cloud import firestore

            payload = {"last_heartbeat_at": now_iso}
            if latest_logs is not None:
                payload["logs"] = latest_logs[-1000:]
                payload["log_count"] = len(latest_logs)
                payload["logs_saved_at"] = now_iso
            ref = (
                firestore.Client(project=project_id)
                .collection("devices")
                .document(device_id)
                .collection("runs")
                .document(str(run_id))
            )
            ref.set(payload, merge=True)
        except Exception as exc:
            print(f"Firestore heartbeat update failed: {exc}")

    history_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "results",
        "agent_history.json",
    )
    try:
        if os.path.exists(history_path):
            with open(history_path, "r", encoding="utf-8") as file:
                records = json.load(file)
            if isinstance(records, list):
                updated = False
                for r in records:
                    if isinstance(r, dict) and r.get("device_id") == device_id and r.get("run_id") == run_id:
                        r["last_heartbeat_at"] = now_iso
                        if latest_logs is not None:
                            r["logs"] = latest_logs[-1000:]
                            r["log_count"] = len(latest_logs)
                            r["logs_saved_at"] = now_iso
                        updated = True
                        break
                if updated:
                    import tempfile
                    fd, temp_path = tempfile.mkstemp(
                        dir=os.path.dirname(history_path), prefix="history_hb_", suffix=".tmp"
                    )
                    with os.fdopen(fd, "w", encoding="utf-8") as file:
                        json.dump(records, file, ensure_ascii=False, indent=2, default=str)
                    os.replace(temp_path, history_path)
    except Exception:
        pass


def persist_run_logs(
    device_id: str,
    run_id: str,
    captured_logs: list[dict],
    exit_code: int | None = None,
    status_override: str | None = None,
    outcome_override: str | None = None,
):
    """Persist subprocess output and synchronize the process status for one run."""
    import json
    import tempfile

    saved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    local_error = None
    history_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "results",
        "agent_history.json",
    )
    os.makedirs(os.path.dirname(history_path), exist_ok=True)

    if status_override:
        process_status = status_override
    elif exit_code is None:
        process_status = "unknown"
    elif exit_code == 0:
        process_status = "completed"
    elif exit_code == 124:
        process_status = "timeout"
    else:
        process_status = "error"

    try:
        try:
            with open(history_path, "r", encoding="utf-8") as file:
                records = json.load(file)
            if not isinstance(records, list):
                records = []
        except (FileNotFoundError, json.JSONDecodeError):
            records = []

        record = next(
            (
                item for item in records
                if isinstance(item, dict)
                and item.get("device_id") == device_id
                and item.get("run_id") == run_id
            ),
            None,
        )
        if record is None:
            record = {
                "device_id": device_id,
                "run_id": run_id,
                "status": process_status,
                "test_outcome": outcome_override or "unknown",
            }
            records.append(record)

        if exit_code is not None:
            record["process_exit_code"] = exit_code
            record["process_status"] = process_status
            record["process_finished_at"] = saved_at
            # Keep the user-visible process status consistent with the exit code.
            record["status"] = process_status
        elif status_override:
            record["status"] = status_override
            record["process_status"] = status_override
            record["process_finished_at"] = saved_at

        if outcome_override and (not record.get("test_outcome") or record.get("test_outcome") in ("unknown", "pending")):
            record["test_outcome"] = outcome_override

        if not record.get("finished_at"):
            record["finished_at"] = saved_at

        if record.get("duration_seconds") is None and record.get("started_at"):
            try:
                start_dt = datetime.fromisoformat(record["started_at"])
                end_dt = datetime.fromisoformat(saved_at)
                record["duration_seconds"] = max(0.0, round((end_dt - start_dt).total_seconds(), 1))
            except Exception:
                pass

        record["logs"] = captured_logs[-1000:]
        record["log_count"] = len(captured_logs)
        record["logs_saved_at"] = saved_at

        fd, temp_path = tempfile.mkstemp(
            dir=os.path.dirname(history_path), prefix="history_logs_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(records, file, ensure_ascii=False, indent=2, default=str)
            os.replace(temp_path, history_path)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
    except Exception as exc:
        local_error = exc

    project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT")
    cloud_error = None
    if project_id:
        try:
            from google.cloud import firestore

            payload = {
                "device_id": device_id,
                "run_id": run_id,
                "logs": captured_logs[-1000:],
                "log_count": len(captured_logs),
                "logs_saved_at": saved_at,
            }
            if exit_code is not None:
                payload.update({
                    "process_exit_code": exit_code,
                    "process_status": process_status,
                    "process_finished_at": saved_at,
                    "status": process_status,
                })
            elif status_override:
                payload.update({
                    "status": status_override,
                    "process_status": status_override,
                    "process_finished_at": saved_at,
                })

            if outcome_override:
                payload["test_outcome"] = outcome_override
            if not payload.get("finished_at"):
                payload["finished_at"] = saved_at

            ref = (
                firestore.Client(project=project_id)
                .collection("devices")
                .document(device_id)
                .collection("runs")
                .document(str(run_id))
            )
            ref.set(payload, merge=True)
        except Exception as exc:
            cloud_error = exc

    if local_error:
        print(f"Local log history save failed: {local_error}")
    if cloud_error:
        print(f"Firestore log history save failed: {cloud_error}")


def reconcile_stale_record(record: dict) -> bool:
    """
    Check if a record is stuck in 'running' or 'pending' without an active process.
    If truly stale (heartbeat or run timeout exceeded), transition it to 'abandoned'.
    """
    status = str(record.get("status") or "").lower()
    if status not in ("running", "processing", "in_progress"):
        return False

    run_id = str(record.get("run_id") or "")
    now_epoch = datetime.now(timezone.utc).timestamp()

    is_local_run = (
        process is not None
        and process.returncode is None
        and state_run_key == run_id
    )

    if is_local_run:
        if state_started_at is not None and (now_epoch - state_started_at > AGENT_RUN_TIMEOUT_SECONDS):
            is_live = False
        else:
            is_live = True
    else:
        # Cross-instance check: do not declare a remote worker abandoned if heartbeats are fresh
        last_hb = record.get("last_heartbeat_at") or record.get("logs_saved_at") or record.get("started_at")
        started = record.get("started_at")
        is_live = False
        if last_hb and started:
            try:
                hb_epoch = datetime.fromisoformat(last_hb).timestamp()
                started_epoch = datetime.fromisoformat(started).timestamp()
                if (now_epoch - hb_epoch <= STALE_HEARTBEAT_THRESHOLD_SECONDS) and (now_epoch - started_epoch <= AGENT_RUN_TIMEOUT_SECONDS):
                    is_live = True
            except Exception:
                is_live = False

    if not is_live:
        saved_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        record["status"] = "abandoned"
        record["process_status"] = "abandoned"
        record["process_exit_code"] = record.get("process_exit_code") or -1
        if record.get("test_outcome") in (None, "unknown", "pending"):
            record["test_outcome"] = "stale_run_abandoned"
        if not record.get("finished_at"):
            record["finished_at"] = record.get("last_heartbeat_at") or record.get("logs_saved_at") or record.get("started_at") or saved_at
        if not record.get("error"):
            record["error"] = "Процес перервано або контейнер перезапущено (stale run)"
        if record.get("duration_seconds") is None and record.get("started_at") and record.get("finished_at"):
            try:
                s = datetime.fromisoformat(record["started_at"])
                f = datetime.fromisoformat(record["finished_at"])
                record["duration_seconds"] = max(0.0, round((f - s).total_seconds(), 1))
            except Exception:
                pass
        return True
    return False


async def collect_output(proc, run_key: str, device_id: str, run_id: str):
    global state, logs
    captured_logs = []
    code = None
    timed_out = False

    stop_heartbeat = asyncio.Event()

    async def _heartbeat_loop():
        while not stop_heartbeat.is_set():
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
                if stop_heartbeat.is_set():
                    break
                await asyncio.to_thread(update_run_heartbeat, device_id, run_id, captured_logs)
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    heartbeat_task = asyncio.create_task(_heartbeat_loop())

    async def _read_stream():
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            message = line.decode("utf-8", errors="replace").rstrip("\r\n")
            captured_logs.append({
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "message": message,
            })
            if state_run_key == run_key:
                logs.append(message)
                del logs[:-150]
        return await proc.wait()

    try:
        code = await asyncio.wait_for(
            _read_stream(),
            timeout=AGENT_RUN_TIMEOUT_SECONDS,
        )
        exit_message = f"Process exited with code {code}"
        captured_logs.append({
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "message": exit_message,
        })
        if state_run_key == run_key:
            logs.append(exit_message)

    except asyncio.TimeoutError:
        timed_out = True
        code = 124
        timeout_message = f"Agent execution timed out after {AGENT_RUN_TIMEOUT_SECONDS}s"
        captured_logs.append({
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "message": timeout_message,
        })
        if state_run_key == run_key:
            logs.append(timeout_message)
        try:
            if proc.returncode is None:
                proc.kill()
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except Exception:
            pass

    except Exception as exc:
        code = proc.returncode if proc.returncode is not None else 1
        error_message = f"Output reader error: {exc}"
        captured_logs.append({
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "message": error_message,
        })
        if state_run_key == run_key:
            logs.append(error_message)
        try:
            if proc.returncode is None:
                proc.kill()
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except Exception:
            pass

    finally:
        stop_heartbeat.set()
        heartbeat_task.cancel()

        final_status = "timeout" if timed_out else ("completed" if code == 0 else "error")
        final_outcome = "timeout" if timed_out else None

        if state_run_key == run_key:
            state = final_status

        try:
            await asyncio.to_thread(
                persist_run_logs,
                device_id,
                run_id,
                captured_logs,
                code,
                status_override=final_status,
                outcome_override=final_outcome,
            )
        except Exception as persist_err:
            print(f"Error persisting logs: {persist_err}")


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
    global process, state, state_device_id, state_run_key, state_started_at, logs

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
        state_started_at = datetime.now(timezone.utc).timestamp()
        env = os.environ.copy()
        env["TEST_URL"] = url
        env["USER_NAME"] = request.first_name.strip()
        env["USER_SURNAME"] = request.last_name.strip()
        env["USER_GROUP"] = request.group_name.strip()
        env["HEADLESS"] = "true"
        env["DEVICE_ID"] = device_id
        env["RUN_ID"] = run_key
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
        record_run_start(device_id, run_key, url, request.model)
        asyncio.create_task(collect_output(process, run_key, device_id, run_key))

    return {
        "status": "running",
        "run_id": run_key,
        "message": "Agent started",
    }


@app.get("/api/status")
async def get_status(
    x_api_token: str | None = Header(default=None),
    x_device_token: str | None = Header(default=None),
):
    global state
    authorize(x_api_token)
    device_id = device_owner_id(x_device_token)

    # 1. If this instance has an active in-memory run for this device
    if state_device_id == device_id and state == "running":
        if process is not None and process.returncode is not None:
            state = "completed" if process.returncode == 0 else "error"
        return {"status": state, "logs": logs[-50:]}

    # 2. Check shared Firestore (or local file fallback) for remote runs on other instances
    import json
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT")
    latest_run = None

    if project_id:
        try:
            from google.cloud import firestore

            db = firestore.Client(project=project_id)
            query = (
                db.collection("devices")
                .document(device_id)
                .collection("runs")
                .order_by("started_at", direction=firestore.Query.DESCENDING)
                .limit(1)
            )
            docs = list(query.stream())
            if docs:
                latest_run = docs[0].to_dict()
        except Exception as exc:
            print(f"Firestore status lookup failed: {exc}")
    else:
        history_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "results", "agent_history.json"
        )
        try:
            if os.path.exists(history_path):
                with open(history_path, "r", encoding="utf-8") as file:
                    records = json.load(file)
                if isinstance(records, list):
                    device_runs = [
                        r for r in records
                        if isinstance(r, dict) and r.get("device_id") == device_id
                    ]
                    if device_runs:
                        device_runs.sort(key=lambda r: r.get("started_at", ""), reverse=True)
                        latest_run = device_runs[0]
        except Exception:
            pass

    if latest_run and isinstance(latest_run, dict):
        run_status = str(latest_run.get("status") or "").lower()
        formatted_logs = [
            l.get("message", "") if isinstance(l, dict) else str(l)
            for l in latest_run.get("logs", [])
        ][-50:]

        if run_status in ("running", "processing", "in_progress"):
            # Check if truly stale across instances
            if reconcile_stale_record(latest_run):
                if project_id:
                    try:
                        db.collection("devices").document(device_id).collection("runs").document(str(latest_run["run_id"])).set(latest_run, merge=True)
                    except Exception:
                        pass
                return {"status": latest_run["status"], "logs": formatted_logs}
            else:
                # Still actively running on another container instance!
                return {"status": "running", "logs": formatted_logs}
        elif run_status in ("completed", "error", "timeout", "abandoned"):
            finished_at = latest_run.get("finished_at")
            if finished_at:
                try:
                    fin_epoch = datetime.fromisoformat(finished_at).timestamp()
                    # Return recent terminal status within 60s of finish so client detects completion
                    if datetime.now(timezone.utc).timestamp() - fin_epoch < 60:
                        return {"status": run_status, "logs": formatted_logs}
                except Exception:
                    pass

    return {
        "status": state if state_device_id == device_id else "idle",
        "logs": logs[-50:] if state_device_id == device_id else [],
    }


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
            for record in records:
                if isinstance(record, dict) and reconcile_stale_record(record):
                    try:
                        db.collection("devices").document(device_id).collection("runs").document(str(record["run_id"])).set(record, merge=True)
                    except Exception as err:
                        print(f"Failed to persist reconciled firestore record: {err}")

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

    modified_any = False
    for item in records:
        if reconcile_stale_record(item):
            modified_any = True

    if modified_any:
        try:
            with open(history_path, "r", encoding="utf-8") as file:
                all_records = json.load(file)
            if isinstance(all_records, list):
                rec_map = {str(r.get("run_id")): r for r in records if isinstance(r, dict)}
                for i, r in enumerate(all_records):
                    if isinstance(r, dict) and str(r.get("run_id")) in rec_map:
                        all_records[i] = rec_map[str(r.get("run_id"))]
                import tempfile
                fd, temp_path = tempfile.mkstemp(
                    dir=os.path.dirname(history_path), prefix="history_reconcile_", suffix=".tmp"
                )
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    json.dump(all_records, file, ensure_ascii=False, indent=2, default=str)
                os.replace(temp_path, history_path)
        except Exception as exc:
            print(f"Failed to persist reconciled local records: {exc}")

    records.sort(
        key=lambda item: item.get("started_at", "")
        if isinstance(item, dict) else "",
        reverse=True,
    )
    return {"history": records}
