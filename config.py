import os
from dataclasses import dataclass

from dotenv import load_dotenv


load_dotenv()


@dataclass(frozen=True)
class Settings:
	target_url: str = os.getenv(
		"TARGET_URL",
		"https://quiz-web-wzr7.onrender.com/",
	)
	gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
	gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
	headless: bool = os.getenv("HEADLESS", "false").lower() == "true"
	results_dir: str = os.getenv("RESULTS_DIR", "results")
	screenshots_dir: str = os.getenv("SCREENSHOTS_DIR", "screenshots")


settings = Settings()
