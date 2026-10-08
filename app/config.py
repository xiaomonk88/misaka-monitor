"""全局配置：从环境变量 / .env 读取，提供带默认值的强类型访问。"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录（app/ 的上一级）
BASE_DIR = Path(__file__).resolve().parent.parent

# 加载 .env（若存在）
load_dotenv(BASE_DIR / ".env")


def _get(key: str, default: str) -> str:
    val = os.getenv(key)
    return val if val not in (None, "") else default


class Settings:
    # 会话密钥
    secret_key: str = _get("APP_SECRET_KEY", "") or secrets.token_hex(32)

    # 时区（展示用）
    display_tz: str = _get("DISPLAY_TZ", "Asia/Shanghai")


    # 数据目录
    data_dir: Path = Path(_get("DATA_DIR", str(BASE_DIR / "data")))

    # 调度
    catalog_sync_hours: float = float(_get("CATALOG_SYNC_HOURS", "24"))
    min_interval_seconds: int = int(_get("MIN_INTERVAL_SECONDS", "15"))

    # 外部 API
    misaka_api_base: str = _get("MISAKA_API_BASE", "https://app.misaka.io/api").rstrip("/")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "misaka.db"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path.as_posix()}"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
