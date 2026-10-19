"""Cloud Runを含むコンテナ環境向けのAPI起動エントリーポイント。"""

import os

import uvicorn


def main() -> None:
    """TLS終端の前段を信頼して起動する。

    **`proxy_headers` を有効にしないと、リダイレクトが `http://` を返す。**
    Cloud RunはTLSを前段で終端するため、アプリ側は自分がHTTPSで提供されていることを
    知らない。FastAPIが `/themes` から `/themes/` へ307を返すとき、`http://` の絶対URLに
    なり、ブラウザはHTTPSページからの遷移をMixed Contentとして遮断する。画面には
    `fail to fetch` としか出ないため、原因が分かりにくい。

    `forwarded_allow_ips="*"` はCloud Runの内部プロキシからの `X-Forwarded-Proto` を
    受け取るため。コンテナへ直接到達する経路は無く、受信はCloud Runの前段に限られる。
    """
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=port,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
