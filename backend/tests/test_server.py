from typing import Any

from app import server


def test_server_uses_cloud_run_port_and_trusts_the_tls_terminator(monkeypatch) -> None:
    """Cloud RunのPORTで待ち受け、前段のTLS終端を信頼すること。

    **`proxy_headers` が無いとリダイレクトが `http://` を返す。** FastAPIが
    `/themes` から `/themes/` へ307を返すとき、HTTPSページからHTTPへの遷移に
    なり、ブラウザがMixed Contentとして遮断する。画面には `fail to fetch` としか
    出ないため原因が分かりにくい。実際に公開デモで踏んだ。

    uvicornの既定値はバージョンで変わる（手元の0.54では有効、デプロイ済みの
    イメージでは無効だった）。`requirements.txt` が範囲指定なので、明示しないと
    ビルドの時期で挙動が変わる。
    """
    called_with: dict[str, Any] = {}

    def fake_run(app: str, **kwargs: Any) -> None:
        called_with.update(app=app, **kwargs)

    monkeypatch.setenv("PORT", "9090")
    monkeypatch.setattr(server.uvicorn, "run", fake_run)

    server.main()

    assert called_with["app"] == "app.main:app"
    assert called_with["host"] == "0.0.0.0"
    assert called_with["port"] == 9090
    assert called_with["proxy_headers"] is True, "リダイレクトがhttpへ落ちる"
    assert called_with["forwarded_allow_ips"] == "*"
