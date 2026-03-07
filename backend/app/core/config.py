from pydantic_settings import BaseSettings
from typing import Optional
import os


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    # App
    APP_NAME: str = "DNS Manager"
    APP_VERSION: str = "2.0.0"
    LOG_LEVEL: str = "info"
    
    # Database
    DATABASE_URL: str = "mysql+aiomysql://dns_admin:changeme-password@mariadb:3306/dns_manager"
    
    # JWT Auth
    JWT_SECRET_KEY: str = "dns-manager-super-secret-key-change-me-2026"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440  # 24 Stunden
    
    # PowerDNS Servers
    # Format: name|url|api_key,name|url|api_key
    PDNS_SERVERS: str = ""
    
    class Config:
        env_file = ".env"
        case_sensitive = True
    
    def get_pdns_servers(self) -> list[dict]:
        """Parse PDNS_SERVERS env var into list of server configs."""
        servers = []
        if not self.PDNS_SERVERS:
            return servers
        
        for entry in self.PDNS_SERVERS.split(","):
            entry = entry.strip()
            if not entry:
                continue
            parts = entry.split("|")
            if len(parts) == 3:
                servers.append({
                    "name": parts[0].strip(),
                    "url": parts[1].strip().rstrip("/"),
                    "api_key": parts[2].strip(),
                })
        return servers


settings = Settings()
