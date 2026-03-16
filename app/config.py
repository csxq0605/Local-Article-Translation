from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key, value)


ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    root_dir: Path = ROOT_DIR
    workspace_dir: Path = ROOT_DIR / "workspace"
    uploads_dir: Path = workspace_dir / "uploads"
    assets_dir: Path = workspace_dir / "assets"
    state_dir: Path = workspace_dir / "state"
    static_dir: Path = ROOT_DIR / "app" / "static"
    app_host: str = os.getenv("APP_HOST", "127.0.0.1")
    app_port: int = int(os.getenv("APP_PORT", "8000"))
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
    deepseek_base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    deepseek_temperature: float = float(os.getenv("DEEPSEEK_TEMPERATURE", "1.3"))
    request_timeout_seconds: int = 180
    translation_chunk_chars: int = 2200


settings = Settings()


def ensure_directories() -> None:
    for path in (
        settings.workspace_dir,
        settings.uploads_dir,
        settings.assets_dir,
        settings.state_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)
