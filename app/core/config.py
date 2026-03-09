from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    OLLAMA_URL: str = "http://localhost:11434"
    MODEL_NAME: str = "mistral"
    BACKEND_URL: str = "http://127.0.0.1:8000"
    
    class Config:
        env_file = ".env"


settings = Settings()
