# browser-agent

`browser-agent` is an asynchronous Python browser automation agent that uses Playwright for web interaction and Google Gemini for next-action decisions.

It is designed for guided form/test workflows: it opens a target page, inspects interactive elements, optionally captures screenshots, asks Gemini for one structured action at a time, executes that action, and stores execution history.

## What it does

Based on the current source code, the agent can:

- Launch Chromium with Playwright (`main.py`)
- Open a configured URL and inspect page state (`browser.py`)
- Extract interactive elements (`input`, `button`, `textarea`, `select`, `a`, `img`, and selected ARIA-role controls)
- Auto-fill initial user fields when present (`surname`, `name`, `grp`) and click a start button before using AI (`agent.py`)
- Request exactly one structured action from Gemini (`llm.py`, `prompts.py`)
- Execute supported actions and stop on completion or safety conditions (`agent.py`)
- Optionally attach screenshot bytes to Gemini requests when vision is enabled (`llm.py`)
- Save run history to `results/agent_history.json` (`agent.py`)

## Supported action types

The action schema in `models.py` defines these action types:

- `fill` - fill a text field
- `click` - click an element
- `select` - select an option, radio, or checkbox
- `wait` - wait for the page to update
- `finish` - end because task is complete
- `stop` - end because agent should not continue

## High-level execution flow

1. `main.py` starts Playwright, launches Chromium, creates a page, and initializes `BrowserController`, `GeminiClient`, and `BrowserAgent`.
2. `BrowserAgent.run()` opens `TEST_URL`.
3. For each step (up to `MAX_STEPS`):
   - Inspect URL, title, body text, and interactive elements.
   - If loading indicators are detected, wait and continue.
   - Try bootstrap actions (fill known identity fields, click start button).
   - Optionally capture a screenshot (`ENABLE_VISION=true`).
   - Ask Gemini for one JSON action.
   - Execute the action.
   - Stop on `finish`, `stop`, or repeated-action loop (`MAX_SAME_ACTION`).
4. Save history to `results/agent_history.json`.

## Requirements and runtime prerequisites

- Python 3.10+ (the code uses `str | None` type syntax)
- Access to a Gemini API key (`GEMINI_API_KEY`)
- Playwright Python package and Chromium browser runtime

Dependencies declared in `requirements.txt`:

- `playwright`
- `python-dotenv`
- `google-genai`
- `pydantic`

## Installation and setup

1. Clone the repository.
2. Create and activate a virtual environment.
3. Install dependencies.
4. Install Playwright Chromium browser binaries.
5. Configure environment variables.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env
```

Edit `.env` with your values (especially `GEMINI_API_KEY`).

## Configuration

Configuration is loaded by `config.py` from environment variables (via `python-dotenv`).

| Variable | Default | Purpose |
| --- | --- | --- |
| `GEMINI_API_KEY` | none | Required API key for Gemini client initialization |
| `GEMINI_MODEL` | `gemini-3.6-flash` | Gemini model name used for `generate_content` |
| `TEST_URL` | `https://quiz-web-wzr7.onrender.com/` | Initial URL opened by the agent |
| `USER_SURNAME` | empty string | Bootstrap value for `surname` input |
| `USER_NAME` | empty string | Bootstrap value for `name` input |
| `USER_GROUP` | empty string | Bootstrap value for `grp` input |
| `MAX_STEPS` | `60` | Maximum run loop steps |
| `ENABLE_VISION` | `true` | If true, screenshots are captured and attached to Gemini requests |
| `HEADLESS` | `false` | If true, launch Chromium in headless mode |
| `MAX_SAME_ACTION` | `3` | Loop-protection threshold for repeated identical actions |

## Usage

Run the entry point:

```bash
python main.py
```

During execution, the agent prints:

- Current step number
- Page title and URL
- Discovered interactive elements
- Chosen action, element ID, value, and reason

## Generated artifacts

- `screenshots/step_XXX.png` when `ENABLE_VISION=true`
- `results/agent_history.json` after each run

The repository `.gitignore` currently excludes `screenshots/` and `results/` for newly generated files.

## Project structure

```text
/home/runner/work/browser-agent/browser-agent/
├── main.py          # Application entry point
├── agent.py         # Agent loop, bootstrap logic, action execution, history persistence
├── browser.py       # Playwright wrapper for inspect/click/fill/select/screenshot/wait
├── llm.py           # Gemini client integration and retry handling
├── prompts.py       # System prompt and prompt builder
├── models.py        # Pydantic action/page models
├── config.py        # Environment-based settings
├── requirements.txt # Python dependencies
├── .env.example     # Example environment variables
└── screenshots/     # Existing screenshot samples in repository
```

## Security and authorization considerations

- Keep `GEMINI_API_KEY` in `.env` only; do not commit it.
- The prompt restricts the model from unrelated navigation and script execution, but browser automation still performs real page actions. Run only against authorized targets.
- Review and limit values in `.env` before execution, especially URL and identity fields.
- Generated history and screenshots may contain page content; handle and store them appropriately.

## Development and testing status

No repository-local test, lint, or formatter configuration files were found (for example `pytest.ini`, `pyproject.toml`, or `setup.cfg`).

## Troubleshooting

- `RuntimeError: GEMINI_API_KEY is not configured.`
  - Set `GEMINI_API_KEY` in `.env`.
- Browser launch or Playwright runtime errors
  - Ensure Playwright dependencies are installed and run `python -m playwright install chromium`.
- Agent appears to stall on loading pages
  - Check `TEST_URL` availability and page behavior.
- Agent stops due to repeated action loop
  - Review `MAX_SAME_ACTION`, page state, and model output reasoning.

## Contributing

There is no contributor guide file in the repository. If you plan to contribute, open an issue or pull request describing the proposed change.

## License

No license file was found in this repository snapshot.
