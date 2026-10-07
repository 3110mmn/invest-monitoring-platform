"""
アプリケーション設定
pydantic-settings で .env を読み込む
"""
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic_settings import BaseSettings

_PROJECT_ROOT = Path(__file__).parent.parent.parent


class Settings(BaseSettings):
    database_url: str = "postgresql://invest:invest@localhost:5432/invest"
    database_environment_override: Literal["development", "test", "demo", "production"] | None = None
    # 公開APIではPostgreSQLセッションとHTTP変更操作を読み取り専用にする。
    database_read_only: bool = False
    backfill_years: int = 5
    cors_origins: list[str] = ["http://localhost:3000"]
    jquants_api_key: str | None = None
    jquants_base_url: str = "https://api.jquants.com/v2"
    jquants_timeout_seconds: float = 30.0
    # プランごとのレート制限（回/分）。Free=5 / Light=60 / Standard=120 / Premium=500
    jquants_requests_per_minute: int = 5
    # プランが提供しない直近日数と、遡れる年数。Freeは12週間(84日)前まで・2年分
    jquants_history_lag_days: int = 84
    jquants_history_years: int = 2
    # 分析層（Parquet）のルート。`gs://bucket/lake` でもローカルパスでもよい。
    # 配下の `observed/market_price` と `observed/financial_summary` を読む。
    # **価格と財務はここからしか読まない。** 未設定だと両方のAPIが503になる。
    # 公開デモはデモ用バケットを指す。分離はバケットの権限で担保しており、この値の
    # 正しさには依存しない。
    parquet_lake: str | None = None
    raw_data_path: str = "data/raw"
    # rawを永続化するGCS prefix。**取得を実行する環境では必ず設定する。**
    # rawは原本で、Parquetもここから作り直す。未設定だと手元にしか残らない。
    raw_data_uri: str | None = None
    management_api_enabled: bool = False
    management_api_key: str | None = None
    # 一般公開デモでのみ、期限付き投資枠の作成・更新を許可する。
    public_demo_write_enabled: bool = False
    public_demo_mandate_ttl_hours: int = 1
    public_demo_max_active_mandates: int = 20
    public_demo_max_assignments_per_mandate: int = 20
    public_demo_writes_per_minute: int = 20

    # .env はカレントディレクトリではなくプロジェクトルート基準で探す。
    # 実行場所（リポジトリルート / backend / CI）によって読めたり読めなかったりするのを避ける。
    # 後に指定したファイルが優先されるため、backend/.env がルートの .env を上書きする。
    model_config = {
        "env_file": (_PROJECT_ROOT / ".env", _PROJECT_ROOT / "backend" / ".env"),
        "env_file_encoding": "utf-8",
        # 廃止済みの設定が既存の .env に残っていても無視する。
        "extra": "ignore",
    }

    @property
    def database_environment(self) -> str:
        """接続先がどの環境かを返す。

        ローカルでは開発DBと実データDBで画面の見た目が変わらないため、
        実データを開発DBだと思って編集する事故が起きる。接続先を画面へ出せるように、
        接続文字列そのものではなく分類だけを公開する。
        """
        if self.database_environment_override is not None:
            return self.database_environment_override
        parsed = urlparse(self.database_url)
        host = parsed.hostname or ""
        database = (parsed.path or "").lstrip("/")

        if host in ("localhost", "127.0.0.1", "postgres", "host.docker.internal"):
            return "test" if database.endswith("_test") else "development"
        if "demo" in database:
            return "demo"
        return "production"

    @property
    def database_username(self) -> str | None:
        return urlparse(self.database_url).username

    @property
    def daily_update_script(self) -> Path:
        return Path(__file__).parent.parent / "scripts" / "daily_update.py"

    @property
    def jquants_sync_script(self) -> Path:
        return Path(__file__).parent.parent / "scripts" / "jquants_sync.py"

    @property
    def raw_data_dir(self) -> Path:
        p = Path(self.raw_data_path)
        if p.is_absolute():
            return p
        return Path(__file__).parent.parent.parent / p


settings = Settings()
