from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)
    app_secret: str
    app_base_url: str = "http://localhost:8000"
    cookie_secure: bool = True
    admin_username: str = "admin"
    admin_password: str
    mail_domain: str = "mailus.aclclouds.online"
    database_url: str
    mailbox_quota_mb: int = 500
    session_max_age_seconds: int = 3600
    registration_enabled: bool = True
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_starttls: bool = True
    max_attachment_mb: int = 10
    storage_dir: str = "/data/custommail"
    bootstrap_admin_enabled: bool = True
    inbound_email_secret: str = ""
    mailumi_webhook_secret: str = ""
    mailumi_api_key: str = ""
    mailumi_send_api_key: str = ""

settings = Settings()
