import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./medical_voice_assistant.db")
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    DEEPGRAM_API_KEY: str = os.getenv("DEEPGRAM_API_KEY", "")
    DAILY_API_KEY: str = os.getenv("DAILY_API_KEY", "")
    DAILY_API_URL: str = os.getenv("DAILY_API_URL", "https://api.daily.co/v1")
    VOICE_TRANSPORT: str = "websocket"
    SARVAM_API_KEY: str = ""
    
    # Google Calendar Credentials JSON contents
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "")
    GOOGLE_SERVICE_ACCOUNT_JSON: str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    GOOGLE_CALENDAR_ID: str = os.getenv("GOOGLE_CALENDAR_ID", "primary")

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
