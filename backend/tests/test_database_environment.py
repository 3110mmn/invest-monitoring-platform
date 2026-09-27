"""接続先の分類が、取り違えの起きる組み合わせを正しく見分けることを確認する。

ローカルでは開発DBと実データDBで画面の見た目が変わらない。この分類を誤ると、
実データを開発だと思って編集する事故につながる。
"""

import pytest

from app.config import Settings


@pytest.mark.parametrize(
    ("database_url", "expected"),
    [
        ("postgresql://invest:x@localhost:5432/invest", "development"),
        ("postgresql://invest:x@127.0.0.1:5432/invest", "development"),
        ("postgresql://invest:x@host.docker.internal:5432/invest", "development"),
        ("postgresql://invest:x@localhost:5432/invest_test", "test"),
        ("postgresql://app_reader:x@ep-a.ap-southeast-2.aws.neon.tech/invest_monitoring_demo", "demo"),
        ("postgresql://app_writer:x@ep-b.ap-southeast-2.aws.neon.tech/appdb", "production"),
    ],
)
def test_database_environment_classification(database_url: str, expected: str):
    assert Settings(database_url=database_url).database_environment == expected


def test_role_does_not_change_the_classification():
    """判定はホストとデータベース名だけで行う。ロール名は見ない。

    ロールで判定すると、同じDBへ別のロールで繋いだときに分類が変わる。画面のバナーは
    「どのデータに触っているか」を示すものなので、権限ではなく接続先で決める。
    """
    host_and_db = "ep-b.ap-southeast-2.aws.neon.tech/appdb"
    classifications = {
        Settings(database_url=f"postgresql://{role}:x@{host_and_db}").database_environment
        for role in ("app_reader", "app_writer", "owner")
    }

    assert classifications == {"production"}


def test_remote_database_is_never_treated_as_development():
    """判定漏れは実データを開発扱いにする方向へ倒れてはいけない。"""
    remote = Settings(
        database_url="postgresql://someone:x@unknown-host.example.com/whatever"
    )

    assert remote.database_environment == "production"
