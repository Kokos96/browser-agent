import os

from dotenv import load_dotenv


load_dotenv()


class Settings:
    gemini_api_key = os.getenv("GEMINI_API_KEY")

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
            "60"
        )
    )

    enable_vision = (
        os.getenv(
            "ENABLE_VISION",
            "true"
        ).lower() == "true"
    )

    headless = (
        os.getenv(
            "HEADLESS",
            "false"
        ).lower() == "true"
    )

    # Скільки разів поспіль можна виконувати
    # однакову дію без зміни сторінки.
    max_same_action = int(
        os.getenv(
            "MAX_SAME_ACTION",
            "3"
        )
    )


settings = Settings()