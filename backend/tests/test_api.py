from app.config import settings


def theme_payload(key: str = "rates") -> dict:
    return {
        "theme_key": key,
        "theme_name": "Rates",
        "strategy_id": 1,
        "description": "Interest-rate theme",
    }


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_read_only_mode_allows_reads_and_rejects_writes(client, monkeypatch):
    monkeypatch.setattr(settings, "database_read_only", True)

    assert client.get("/health").status_code == 200
    response = client.post("/api/themes/", json=theme_payload())

    assert response.status_code == 405
    assert response.json()["error"]["message"] == "API is running in read-only mode"
    assert response.json()["error"]["code"] == "method_not_allowed"


def test_readiness_without_ingestion_history_is_ready(client):
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "database": "ok",
        # 価格は分析層からしか読まないので、到達性を readiness に出す。
        "analytics": "ok",
        # テストは専用DB（invest_test）に対して実行される。
        "database_environment": "test",
        "latest_ingestions": [],
    }


def test_readiness_reports_latest_status_for_each_job_type(client, db):
    source_id = db.execute(
        "INSERT INTO data_source (source_key, source_name) VALUES (?, ?)",
        ("jquants", "J-Quants"),
    ).lastrowid
    db.executemany(
        """
        INSERT INTO ingestion_run (job_type, source_id, status, started_at, finished_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            ("master", source_id, "failed", "2026-09-03 00:00:00", "2026-09-03 00:01:00"),
            ("master", source_id, "succeeded", "2026-09-04 00:00:00", "2026-09-04 00:01:00"),
            ("prices", source_id, "partial", "2026-09-04 01:00:00", "2026-09-04 01:01:00"),
        ],
    )

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "degraded",
        "database": "ok",
        "analytics": "ok",
        "database_environment": "test",
        "latest_ingestions": [
            {
                "job_type": "master",
                "status": "succeeded",
                    "finished_at": "2026-09-04T00:01:00+00:00",
            },
            {
                "job_type": "prices",
                "status": "partial",
                    "finished_at": "2026-09-04T01:01:00+00:00",
            },
        ],
    }


def test_management_api_is_hidden_when_disabled(client, monkeypatch):
    monkeypatch.setattr(settings, "management_api_enabled", False)
    response = client.get("/api/data/runs")
    assert response.status_code == 404


def test_management_api_requires_valid_key(client, monkeypatch):
    monkeypatch.setattr(settings, "management_api_enabled", True)
    monkeypatch.setattr(settings, "management_api_key", "test-secret")

    assert client.get("/api/data/runs").status_code == 401
    assert client.get(
        "/api/data/runs",
        headers={"X-Admin-Key": "wrong"},
    ).status_code == 401


def test_management_mode_requires_key_for_domain_writes(client, monkeypatch):
    monkeypatch.setattr(settings, "management_api_enabled", True)
    monkeypatch.setattr(settings, "management_api_key", "test-secret")

    assert client.get("/api/themes/").status_code == 200
    assert client.post("/api/themes/", json=theme_payload()).status_code == 401
    assert client.post(
        "/api/themes/",
        json=theme_payload(),
        headers={"X-Admin-Key": "wrong"},
    ).status_code == 401

    created = client.post(
        "/api/themes/",
        json=theme_payload(),
        headers={"X-Admin-Key": "test-secret"},
    )
    assert created.status_code == 201


def test_management_mode_fails_closed_without_configured_key(client, monkeypatch):
    monkeypatch.setattr(settings, "management_api_enabled", True)
    monkeypatch.setattr(settings, "management_api_key", None)

    response = client.post("/api/themes/", json=theme_payload())

    assert response.status_code == 503
    assert response.json()["error"]["message"] == "Management API is not configured"


def test_management_api_lists_and_gets_ingestion_runs(client, db, monkeypatch):
    monkeypatch.setattr(settings, "management_api_enabled", True)
    monkeypatch.setattr(settings, "management_api_key", "test-secret")
    source_id = db.execute(
        "INSERT INTO data_source (source_key, source_name) VALUES (?, ?)",
        ("jquants-runs", "J-Quants"),
    ).lastrowid
    run_id = db.execute(
        """
        INSERT INTO ingestion_run (job_type, source_id, status)
        VALUES (?, ?, ?)
        """,
        ("prices", source_id, "succeeded"),
    ).lastrowid
    headers = {"X-Admin-Key": "test-secret"}

    listed = client.get("/api/data/runs", headers=headers)
    fetched = client.get(f"/api/data/runs/{run_id}", headers=headers)

    assert listed.status_code == 200
    assert listed.json()[0]["ingestion_run_id"] == run_id
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "succeeded"


def test_management_backfill_validates_year_range(client, monkeypatch):
    monkeypatch.setattr(settings, "management_api_enabled", True)
    monkeypatch.setattr(settings, "management_api_key", "test-secret")
    response = client.post(
        "/api/data/backfill?years=0",
        headers={"X-Admin-Key": "test-secret"},
    )
    assert response.status_code == 422


def test_create_and_get_theme(client):
    created = client.post("/api/themes/", json=theme_payload())
    assert created.status_code == 201
    theme_id = created.json()["theme_id"]

    fetched = client.get(f"/api/themes/{theme_id}")
    assert fetched.status_code == 200
    assert fetched.json()["theme_key"] == "rates"


def test_duplicate_theme_key_returns_conflict(client):
    assert client.post("/api/themes/", json=theme_payload()).status_code == 201
    assert client.post("/api/themes/", json=theme_payload()).status_code == 409


def test_update_and_soft_delete_theme(client):
    theme_id = client.post("/api/themes/", json=theme_payload()).json()["theme_id"]
    updated = client.put(f"/api/themes/{theme_id}", json={"theme_name": "New name"})
    assert updated.status_code == 200
    assert updated.json()["theme_name"] == "New name"

    deleted = client.delete(f"/api/themes/{theme_id}")
    assert deleted.status_code == 204
    assert client.get(f"/api/themes/{theme_id}").json()["is_active"] is False


def test_missing_theme_returns_not_found(client):
    assert client.get("/api/themes/999").status_code == 404


def test_strategy_route_is_not_shadowed_by_theme_id(client):
    created = client.post(
        "/api/themes/strategies/",
        json={"strategy_key": "macro", "strategy_name": "Macro"},
    )
    assert created.status_code == 201
    response = client.get("/api/themes/strategies/")
    assert response.status_code == 200
    assert "macro" in {strategy["strategy_key"] for strategy in response.json()}


def test_create_investment_target(client):
    response = client.post(
        "/api/investment-targets/",
        json={"target_key": "1306.T", "target_name": "TOPIX ETF", "target_type": "etf"},
    )
    assert response.status_code == 201
    assert response.json()["target_key"] == "1306.T"


def test_duplicate_target_key_returns_conflict(client):
    payload = {"target_key": "1306.T", "target_name": "TOPIX ETF", "target_type": "etf"}
    assert client.post("/api/investment-targets/", json=payload).status_code == 201

    response = client.post("/api/investment-targets/", json=payload)

    assert response.status_code == 409
    assert response.json()["error"]["message"] == "target_key already exists"
    assert response.json()["error"]["code"] == "conflict"


def test_patch_asset_updates_selected_fields(client):
    target_id = client.post(
        "/api/investment-targets/",
        json={"target_key": "1306.T", "target_name": "TOPIX ETF", "target_type": "etf"},
    ).json()["target_id"]

    response = client.patch(f"/api/investment-targets/{target_id}", json={"target_name": "TOPIX連動ETF"})

    assert response.status_code == 200
    assert response.json()["target_name"] == "TOPIX連動ETF"
    assert response.json()["target_key"] == "1306.T"


def test_asset_price_days_are_bounded(client):
    target_id = client.post(
        "/api/investment-targets/",
        json={"target_key": "1306.T", "target_name": "TOPIX ETF", "target_type": "etf"},
    ).json()["target_id"]

    assert client.get(f"/api/investment-targets/{target_id}/prices?days=0").status_code == 422
    assert client.get(f"/api/investment-targets/{target_id}/prices?days=3651").status_code == 422


def test_price_endpoints_return_nothing_when_the_analytics_layer_is_empty(client):
    """価格はPostgreSQLから読まない。

    PostgreSQLへ観測を入れても価格は返らない。分析層が空なら空で返る。値そのものの
    検証は `test_price_source.py` がParquetを与えて行う。この経路が1本であることは、
    公開デモと管理APIが同じコードを通す前提なので、ここで固定する。
    """
    target_id = client.post(
        "/api/investment-targets/",
        json={"target_key": "1306.T", "target_name": "TOPIX ETF", "target_type": "etf"},
    ).json()["target_id"]

    history = client.get(f"/api/investment-targets/{target_id}/prices?days=30")
    latest = client.get("/api/investment-targets/latest-prices")

    assert history.status_code == 200
    assert history.json() == []
    assert latest.status_code == 200
    assert latest.json() == []


def test_theme_constituents_include_weight_and_rationale(client, db):
    """テーマの構成銘柄は、ウェイトと採用理由つきで返る。

    採用理由は投資仮説の記録であり、銘柄マスタ側の属性ではない。
    テーマ内での位置づけとして別の列名で返す。
    """
    # strategy は schema.sql が初期投入しているため、既存を参照する
    strategy_id = db.execute(
        "SELECT strategy_id FROM strategy WHERE strategy_key = 'core'"
    ).fetchone()[0]
    theme_id = db.execute(
        "INSERT INTO theme (theme_key, theme_name, strategy_id) VALUES ('ai', 'AI', ?)",
        (strategy_id,),
    ).lastrowid
    target_id = db.execute(
        "INSERT INTO investment_target (target_key, target_name, target_type) "
        "VALUES ('6758.T', 'ソニー', 'individual_stock')"
    ).lastrowid
    db.execute(
        "INSERT INTO theme_investment_target (theme_id, target_id, basket_weight, rationale) "
        "VALUES (?, ?, 0.4, '画像センサーの寡占')",
        (theme_id, target_id),
    )
    db.commit()

    body = client.get(f"/api/relationships/themes/{theme_id}/investment-targets").json()

    assert len(body) == 1
    assert body[0]["target_key"] == "6758.T"
    assert body[0]["basket_weight"] == 0.4
    assert body[0]["theme_rationale"] == "画像センサーの寡占"
    assert body[0]["relation_is_active"] is True


def test_theme_constituents_are_empty_for_theme_without_targets(client, db):
    """銘柄が紐づいていないテーマでは空配列を返す（404にしない）。"""
    # strategy は schema.sql が初期投入しているため、既存を参照する
    strategy_id = db.execute(
        "SELECT strategy_id FROM strategy WHERE strategy_key = 'core'"
    ).fetchone()[0]
    theme_id = db.execute(
        "INSERT INTO theme (theme_key, theme_name, strategy_id) VALUES ('empty', '空', ?)",
        (strategy_id,),
    ).lastrowid
    db.commit()

    res = client.get(f"/api/relationships/themes/{theme_id}/investment-targets")

    assert res.status_code == 200
    assert res.json() == []


def _seed_theme(db, theme_key="ai", theme_name="AI", strategy_key="satellite"):
    strategy = db.execute(
        "SELECT strategy_id, strategy_name FROM strategy WHERE strategy_key = ?", (strategy_key,)
    ).fetchone()
    theme_id = db.execute(
        "INSERT INTO theme (theme_key, theme_name, strategy_id, description) VALUES (?, ?, ?, ?)",
        (theme_key, theme_name, strategy["strategy_id"], "仮説メモ"),
    ).lastrowid
    db.commit()
    return theme_id, strategy["strategy_name"]


def test_theme_detail_includes_strategy_name(client, db):
    """テーマ詳細は strategy のキーと表示名を含めて返す。

    `strategy_id` だけだと、呼び出し側が strategy 一覧を別途取得して突き合わせる
    必要が生じる。DBで1回JOINすれば済むため、API側で解決する。
    """
    theme_id, strategy_name = _seed_theme(db)

    body = client.get(f"/api/themes/{theme_id}").json()

    assert body["strategy_key"] == "satellite"
    assert body["strategy_name"] == strategy_name
    assert body["description"] == "仮説メモ"


def test_theme_summary_and_detail_agree_on_types(client, db):
    """一覧と詳細で同じ項目の型が食い違わない。

    `/summary` に response_model が無かった頃、`is_active` が一覧では整数、
    詳細では真偽値として返っていた。
    """
    theme_id, _ = _seed_theme(db)

    summary = next(
        row for row in client.get("/api/themes/summary").json() if row["theme_id"] == theme_id
    )
    detail = client.get(f"/api/themes/{theme_id}").json()

    assert summary["is_active"] is True
    assert detail["is_active"] is True
    assert summary["strategy_key"] == detail["strategy_key"]
    assert summary["strategy_name"] == detail["strategy_name"]


def test_theme_detail_returns_404_for_unknown_theme(client):
    """存在しないテーマは404を返す。"""
    assert client.get("/api/themes/9999").status_code == 404


def _seed_theme_and_target(db):
    strategy_id = db.execute(
        "SELECT strategy_id FROM strategy WHERE strategy_key = 'core'"
    ).fetchone()[0]
    theme_id = db.execute(
        "INSERT INTO theme (theme_key, theme_name, strategy_id) VALUES ('ai', 'AI', ?)",
        (strategy_id,),
    ).lastrowid
    target_id = db.execute(
        "INSERT INTO investment_target (target_key, target_name, target_type) "
        "VALUES ('6758.T', 'ソニー', 'individual_stock')"
    ).lastrowid
    db.commit()
    return theme_id, target_id


def test_adding_a_constituent_returns_the_created_row(client, db):
    """銘柄を追加すると、ウェイトと採用理由つきの構成銘柄が返る。"""
    theme_id, target_id = _seed_theme_and_target(db)

    res = client.post(
        f"/api/relationships/themes/{theme_id}/investment-targets",
        json={"target_id": target_id, "basket_weight": 0.3, "rationale": "画像センサー"},
    )

    assert res.status_code == 201
    assert res.json()["target_key"] == "6758.T"
    assert res.json()["basket_weight"] == 0.3
    assert res.json()["theme_rationale"] == "画像センサー"
    assert res.json()["relation_is_active"] is True


def test_removing_a_constituent_keeps_the_record(client, db):
    """テーマから外しても行は消さず、無効な紐付けとして残す。

    いつ何をどういう理由で採用していたかは投資判断の記録であり、
    物理削除すると後から仮説を検証できなくなる。
    """
    theme_id, target_id = _seed_theme_and_target(db)
    client.post(
        f"/api/relationships/themes/{theme_id}/investment-targets",
        json={"target_id": target_id, "basket_weight": 0.3, "rationale": "画像センサー"},
    )

    res = client.delete(f"/api/relationships/themes/{theme_id}/investment-targets/{target_id}")

    assert res.status_code == 204
    rows = client.get(f"/api/relationships/themes/{theme_id}/investment-targets").json()
    assert len(rows) == 1
    assert rows[0]["relation_is_active"] is False
    assert rows[0]["theme_rationale"] == "画像センサー"


def test_re_adding_a_removed_constituent_restores_it(client, db):
    """外した銘柄を再度追加すると有効に戻る。"""
    theme_id, target_id = _seed_theme_and_target(db)
    client.post(
        f"/api/relationships/themes/{theme_id}/investment-targets",
        json={"target_id": target_id, "basket_weight": 0.3},
    )
    client.delete(f"/api/relationships/themes/{theme_id}/investment-targets/{target_id}")

    res = client.post(
        f"/api/relationships/themes/{theme_id}/investment-targets",
        json={"target_id": target_id, "basket_weight": 0.5, "rationale": "再採用"},
    )

    assert res.json()["relation_is_active"] is True
    assert res.json()["basket_weight"] == 0.5
    assert res.json()["theme_rationale"] == "再採用"


def test_adding_to_unknown_theme_or_target_returns_404(client, db):
    """存在しないテーマ・銘柄への紐付けは404にする（FK違反で500にしない）。"""
    theme_id, target_id = _seed_theme_and_target(db)

    assert client.post(
        "/api/relationships/themes/9999/investment-targets", json={"target_id": target_id}
    ).status_code == 404
    assert client.post(
        f"/api/relationships/themes/{theme_id}/investment-targets", json={"target_id": 9999}
    ).status_code == 404
