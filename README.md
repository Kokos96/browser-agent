# browser-agent

`browser-agent` is a Python Playwright automation agent that uses Google Gemini to decide the next safe UI action on a web page.

It is designed for step-by-step web test interaction: the agent inspects the DOM, optionally captures screenshots for vision input, asks Gemini for a single structured action, executes it, and repeats until completion or stop conditions are met.

## What problem this solves

For repetitive browser workflows (especially form-first quiz/test flows), manually scripting every page state transition is slow and brittle. This project provides a constrained agent loop that:

- reads current page state,
- applies deterministic bootstrap actions for known startup fields,
- delegates uncertain next-step decisions to Gemini in a strict JSON schema,
- executes exactly one browser action per step,
- records run history for debugging.

## Main capabilities

- Async Playwright browser control (Chromium)
- Structured DOM inspection of interactive elements
- Optional screenshot-based vision context for Gemini
- Automatic bootstrap filling for `surname`, `name`, and `grp` inputs from environment variables
- Start-button detection for test kickoff (`start`, `start test`, `почати`, `почати тест`)
- Strict action model (`fill`, `click`, `select`, `wait`, `finish`, `stop`)
- Basic repeated-action loop protection
- JSON history output in `results/agent_history.json`

## High-level execution flow

1. `main.py` launches Playwright Chromium and creates `BrowserController` and `GeminiClient`.
2. `BrowserAgent.run()` opens `TEST_URL` and iterates up to `MAX_STEPS`.
3. Each step:
   - inspects page state (`url`, `title`, `body text`, interactive elements),
   - handles loading-page waits,
   - performs deterministic bootstrap actions (fill known fields/start button),
   - optionally saves a screenshot,
   - asks Gemini for one next action,
   - executes that action in the browser.
4. The run stops on `finish`, `stop`, action-loop threshold, or step limit.
5. History is saved to `results/agent_history.json`.

## Requirements

This repository currently provides dependency metadata via `requirements.txt` only.

### Python version

- No explicit version is pinned in project metadata.
- The code uses modern type syntax (`str | None`, `list[dict]`), so use **Python 3.10+**.

### Runtime dependencies

From `requirements.txt`:

- `playwright`
- `python-dotenv`
- `google-genai`
- `pydantic`

## Installation

```bash
cd /home/runner/work/browser-agent/browser-agent
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
python -m playwright install chromium
```

## Configuration

Copy and edit environment variables:

```bash
cp .env.example .env
```

### Environment variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `GEMINI_API_KEY` | Yes | None | API key for Google Gemini; startup fails if missing. |
| `GEMINI_MODEL` | No | `gemini-3.6-flash` | Gemini model name used for action decisions. |
| `TEST_URL` | No | `https://quiz-web-wzr7.onrender.com/` | Initial page URL to open. |
| `USER_SURNAME` | No | empty | Value used to auto-fill `surname` input. |
| `USER_NAME` | No | empty | Value used to auto-fill `name` input. |
| `USER_GROUP` | No | empty | Value used to auto-fill `grp` input. |
| `MAX_STEPS` | No | `60` | Max decision loop steps before exit. |
| `ENABLE_VISION` | No | `true` | If true, screenshots are sent to Gemini. |
| `HEADLESS` | No | `false` | If true, runs browser headless. |
| `MAX_SAME_ACTION` | No | `3` | Consecutive identical-action threshold before stop. |

## Usage

Run the agent:

```bash
python main.py
```

Typical console output includes:

- current step number,
- page title/URL,
- discovered interactive elements,
- chosen action (`fill`, `click`, `select`, etc.),
- stop/finish reason,
- history save path.

### Expected artifacts

- `results/agent_history.json` (always written at end of run)
- `screenshots/step_XXX.png` (when `ENABLE_VISION=true`)

## Common workflows

### 1) Run against the default test URL

1. Set `GEMINI_API_KEY` in `.env`.
2. Optionally set `USER_SURNAME`, `USER_NAME`, `USER_GROUP`.
3. Run `python main.py`.

### 2) Run headless in CI-like environment

Set in `.env`:

```env
HEADLESS=true
ENABLE_VISION=false
```

Then run `python main.py`.

### 3) Limit cost and execution time

Set lower limits, for example:

```env
MAX_STEPS=20
MAX_SAME_ACTION=2
```

## Project structure

```text
browser-agent/
├── main.py          # Entry point: launches browser and agent
├── agent.py         # Main control loop and action execution
├── browser.py       # Playwright interaction and page inspection
├── llm.py           # Gemini client and retry handling
├── models.py        # Pydantic schemas for page/action data
├── prompts.py       # System prompt and dynamic prompt builder
├── config.py        # Environment-based settings
├── .env.example     # Example runtime configuration
├── requirements.txt # Python dependencies
└── screenshots/     # Screenshot output directory (runtime)
```

## Development notes

This repository does not currently define dedicated lint, format, type-check, or test commands in project metadata.

Minimal local validation for contributors:

```bash
python -m py_compile *.py
```

## Testing and troubleshooting

### Common issues

### `GEMINI_API_KEY is not configured.`

- Ensure `.env` exists and includes `GEMINI_API_KEY=...`.
- Confirm you run from repository root so `python-dotenv` loads `.env`.

### Playwright browser errors (missing executable)

Install browser binaries:

```bash
python -m playwright install chromium
```

### Agent repeats the same action and stops

- Increase `MAX_SAME_ACTION` if the page legitimately needs repeated interactions.
- Review `results/agent_history.json` to inspect why Gemini kept selecting the same action.

### Gemini rate-limit or retry errors

- The client parses retry hints and retries once after delay.
- If failures continue, reduce run frequency and/or `MAX_STEPS`.

## Security considerations

- Keep `.env` private; never commit API keys.
- Limit `TEST_URL` to trusted targets you are authorized to automate.
- The agent prompt explicitly constrains unsafe behaviors, but model output should still be treated as untrusted automation input.

## Limitations and possible future work

Current implementation limits:

- Single-agent, single-page-step loop (no multi-tab orchestration).
- No packaged CLI entry point (run via `python main.py`).
- No built-in unit/integration test suite in this repository.

Possible future improvements:

- Add formal tests for agent decision and browser-controller behaviors.
- Provide a packaged CLI command and installable module structure.
- Add configurable logging and richer run reports.

## Contributing

1. Fork and create a feature branch.
2. Create a virtual environment and install dependencies.
3. Make focused changes.
4. Run minimal validation (`python -m py_compile *.py`) and manual run checks.
5. Open a pull request with clear scope and rationale.

## License

No license file is currently present in this repository. Do not assume redistribution rights until a license is added by the maintainers.
