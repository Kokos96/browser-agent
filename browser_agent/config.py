import os

from dotenv import load_dotenv


load_dotenv()


class Settings:

    gemini_api_key = os.getenv(
        "GEMINI_API_KEY"
    )

    gemini_model = os.getenv(
        "GEMINI_MODEL",
        "gemini-3.6-flash"
    )

    test_url = os.getenv(
        "TEST_URL",
        "https://quiz-web-wzr7.onrender.com/"
    )

    user_surname = os.getenv(
        "USER_SURNAME",
        ""
    )

    user_name = os.getenv(
        "USER_NAME",
        ""
    )

    user_group = os.getenv(
        "USER_GROUP",
        ""
    )

    max_steps = int(
        os.getenv(
            "MAX_STEPS",
            "80"
        )
    )

    enable_vision = (
        os.getenv(
            "ENABLE_VISION",
            "false"
        ).lower() == "true"
    )

    headless = (
        os.getenv(
            "HEADLESS",
            "false"
        ).lower() == "true"
    )

    max_same_action = int(
        os.getenv(
            "MAX_SAME_ACTION",
            "3"
        )
    )

    test_load_timeout = int(
        os.getenv(
            "TEST_LOAD_TIMEOUT",
            "20"
        )
    )

    auto_submit = (
        os.getenv(
            "AUTO_SUBMIT",
            "true"
        ).lower() == "true"
    )


settings = Settings()