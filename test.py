from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    TEST: str = "ok"
    class Config:
        env_file = ".env"

s = Settings()
print(s.TEST)
