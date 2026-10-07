import json
from decimal import Decimal

import duckdb
import pytest

from app.routers.domain import mandates as mandate_routes
from app.routers.domain import securities as security_routes


def mandate_payload() -> dict:
    return {
        "mandate_name": "Core｜長期市場リターン",
        "status": "active",
        "purpose": "低い意思決定コストで長期市場リターンを得る",
        "allocation_weight": 0.65,
        "expected_return": 0.05,
        "max_drawdown": -0.4,
        "horizon_months": 120,
        "review_cycle": "annual",
        "change_reason": "Initial version",
    }


def create_target(client, key: str = "2559.T") -> int:
    response = client.post(
        "/api/investment-targets/",
        json={"target_key": key, "target_name": "All Country", "target_type": "etf"},
    )
    assert response.status_code == 201
    return response.json()["target_id"]


def test_catalog_security_can_be_assigned_without_watchlisting(client, db, monkeypatch):
    catalog = duckdb.connect()
    catalog.execute("""
        CREATE VIEW security_master AS
        SELECT '72030' AS security_key, '72030' AS jpx_code,
               '7203.T' AS target_key, '架空自動車' AS company_name,
               NULL::VARCHAR AS company_name_english,
               '011' AS product_category_code,
               NULL::VARCHAR AS market_code, '東証' AS market_name,
               NULL::VARCHAR AS sector_17_code, NULL::VARCHAR AS sector_17_name,
               NULL::VARCHAR AS sector_33_code, NULL::VARCHAR AS sector_33_name,
               NULL::VARCHAR AS scale_category
    """)
    monkeypatch.setattr(mandate_routes, "get_analytics_connection", lambda: catalog)
    monkeypatch.setattr(security_routes, "_analytics", lambda: catalog)
    mandate = client.post("/api/mandates/", json=mandate_payload()).json()
    mandate_id = mandate["mandate_id"]

    matches = client.get("/api/securities/?q=架空").json()
    assert [row["security_key"] for row in matches] == ["72030"]

    response = client.put(
        f"/api/mandates/{mandate_id}/securities/72030",
        json={"target_weight": 0.25, "rationale": "Investment plan"},
    )
    assert response.status_code == 200
    assert response.json()[0]["target_key"] == "7203.T"
    target_id = response.json()[0]["target_id"]

    assert client.get(f"/api/investment-targets/{target_id}").json()["watchlist_status"] is None
    assert db.execute(
        "SELECT COUNT(*) AS n FROM watchlist_entry WHERE target_id = ?", (target_id,)
    ).fetchone()["n"] == 0

    repeated = client.put(
        f"/api/mandates/{mandate_id}/securities/72030", json={"target_weight": 0.3},
    )
    assert repeated.status_code == 200
    assert len(repeated.json()) == 1
    assignment_history = db.execute("""
        SELECT target_weight, effective_from, effective_until
        FROM mandate_target_assignment
        WHERE target_id = ? ORDER BY effective_from
    """, (target_id,)).fetchall()
    assert len(assignment_history) == 2
    assert assignment_history[0]["target_weight"] == 0.25
    assert assignment_history[0]["effective_until"] is not None
    assert assignment_history[1]["target_weight"] == 0.3
    assert assignment_history[1]["effective_until"] is None

    removed = client.delete(
        f"/api/mandates/{mandate_id}/investment-targets/{target_id}"
    )
    assert removed.status_code == 204
    assert client.get(f"/api/mandates/{mandate_id}").json()["assignments"] == []
    assert db.execute(
        "SELECT COUNT(*) AS n FROM mandate_target_assignment WHERE target_id = ?",
        (target_id,),
    ).fetchone()["n"] == 2
    restored = client.put(
        f"/api/mandates/{mandate_id}/securities/72030", json={"target_weight": 0.3},
    )
    assert restored.status_code == 200
    assert len(restored.json()) == 1
    unchanged = client.put(
        f"/api/mandates/{mandate_id}/securities/72030", json={"target_weight": 0.3},
    )
    assert unchanged.status_code == 200
    assert db.execute(
        "SELECT COUNT(*) AS n FROM mandate_target_assignment WHERE target_id = ?",
        (target_id,),
    ).fetchone()["n"] == 3

    benchmark_update = client.patch(f"/api/mandates/{mandate_id}", json={
        "benchmark_security_key": "72030",
        "change_reason": "Use catalog security as benchmark",
    })
    assert benchmark_update.status_code == 200
    assert benchmark_update.json()["benchmark_target_id"] == target_id
    assert benchmark_update.json()["benchmark_target_name"] == "架空自動車"

    watched = client.put("/api/securities/72030/watchlist", json={"status": "considering"})
    assert watched.status_code == 200
    assert watched.json()["target_id"] == target_id
    assert client.get(f"/api/investment-targets/{target_id}").json()["watchlist_status"] == "considering"
    assert client.get(f"/api/mandates/{mandate_id}").json()["assignments"][0]["target_id"] == target_id
    catalog.close()


def test_mandate_review_queue_is_one_item_per_mandate(client):
    payload = mandate_payload()
    payload["next_review_at"] = "2026-12-31"
    mandate = client.post("/api/mandates/", json=payload).json()
    for key in ("2559.T", "1306.T"):
        target_id = create_target(client, key)
        response = client.put(
            f"/api/mandates/{mandate['mandate_id']}/investment-targets/{target_id}",
            json={"target_id": target_id, "target_weight": 0.2},
        )
        assert response.status_code == 200

    queue = client.get("/api/mandates/review-queue")
    assert queue.status_code == 200
    assert queue.json() == [{
        "mandate_id": mandate["mandate_id"],
        "mandate_name": mandate["mandate_name"],
        "next_review_at": "2026-12-31",
    }]


def test_mandate_crud_and_assignment_amount(client):
    assert client.put("/api/mandates/capital-budget", json={
        "total_budget": "10000000", "currency": "JPY", "change_reason": "Initial allocation",
    }).status_code == 200
    created = client.post("/api/mandates/", json=mandate_payload())
    assert created.status_code == 201
    mandate = created.json()
    assert mandate["version_no"] == 1
    assert Decimal(mandate["budget_amount"]) == Decimal("6500000")
    assert mandate["assignments"] == []

    target_id = create_target(client)
    assigned = client.put(
        f"/api/mandates/{mandate['mandate_id']}/investment-targets/{target_id}",
        json={
            "target_id": target_id,
            "target_weight": 0.8,
            "minimum_weight": 0.7,
            "maximum_weight": 0.9,
            "rationale": "Core holding",
        },
    )
    assert assigned.status_code == 200
    assert Decimal(assigned.json()[0]["target_amount"]) == Decimal("5200000")

    fetched = client.get(f"/api/mandates/{mandate['mandate_id']}")
    assert fetched.status_code == 200
    assert fetched.json()["allocated_weight"] == 0.8
    assert fetched.json()["unallocated_weight"] == pytest.approx(0.2)


def test_mandate_allows_optional_allocation_fields(client):
    payload = mandate_payload()
    payload.update({"allocation_weight": None, "expected_return": None})
    response = client.post("/api/mandates/", json=payload)
    assert response.status_code == 201
    assert response.json()["budget_amount"] is None


def test_mandate_requires_custom_label_for_other_review_cycle(client):
    payload = mandate_payload()
    payload["review_cycle"] = "other"
    response = client.post("/api/mandates/", json=payload)
    assert response.status_code == 422

    payload["review_cycle_custom"] = "決算発表後"
    response = client.post("/api/mandates/", json=payload)
    assert response.status_code == 201
    assert response.json()["review_cycle_custom"] == "決算発表後"


def test_mandate_revision_closes_previous_assignments_and_clones_current(client, db):
    assert client.put("/api/mandates/capital-budget", json={
        "total_budget": "10000000", "currency": "JPY", "change_reason": "Initial allocation",
    }).status_code == 200
    mandate = client.post("/api/mandates/", json=mandate_payload()).json()
    target_id = create_target(client)
    client.put(
        f"/api/mandates/{mandate['mandate_id']}/investment-targets/{target_id}",
        json={"target_id": target_id, "target_weight": 0.8},
    )
    revised = client.patch(
        f"/api/mandates/{mandate['mandate_id']}",
        json={"allocation_weight": 0.7, "change_reason": "Annual review"},
    )
    assert revised.status_code == 200
    assert revised.json()["version_no"] == 2
    assert Decimal(revised.json()["assignments"][0]["target_amount"]) == Decimal("5600000")
    rows = db.execute("""
        SELECT v.version_no, a.target_weight, a.effective_from, a.effective_until
        FROM mandate_target_assignment a
        JOIN mandate_version v ON v.mandate_version_id = a.mandate_version_id
        WHERE v.mandate_id = ? AND a.target_id = ?
        ORDER BY v.version_no
    """, (mandate["mandate_id"], target_id)).fetchall()
    assert len(rows) == 2
    assert rows[0]["version_no"] == 1 and rows[0]["effective_until"] is not None
    assert rows[1]["version_no"] == 2 and rows[1]["effective_until"] is None


def test_mandate_rejects_invalid_weight_range(client):
    mandate = client.post("/api/mandates/", json=mandate_payload()).json()
    target_id = create_target(client)
    response = client.put(
        f"/api/mandates/{mandate['mandate_id']}/investment-targets/{target_id}",
        json={
            "target_id": target_id,
            "target_weight": 0.8,
            "minimum_weight": 0.7,
            "maximum_weight": 0.75,
        },
    )
    assert response.status_code == 422


def test_mandate_rejects_assignments_over_one(client):
    mandate = client.post("/api/mandates/", json=mandate_payload()).json()
    first = create_target(client, "2559.T")
    second = create_target(client, "1306.T")
    assert client.put(
        f"/api/mandates/{mandate['mandate_id']}/investment-targets/{first}",
        json={"target_id": first, "target_weight": 0.8},
    ).status_code == 200
    response = client.put(
        f"/api/mandates/{mandate['mandate_id']}/investment-targets/{second}",
        json={"target_id": second, "target_weight": 0.3},
    )
    assert response.status_code == 422


def test_mandate_and_theme_are_independent(client):
    mandate = client.post("/api/mandates/", json=mandate_payload()).json()
    assert "theme_id" not in set(mandate)


def test_capital_budget_calculates_mandate_and_target_amounts(client):
    budget = client.put("/api/mandates/capital-budget", json={
        "total_budget": "10000000", "currency": "JPY", "change_reason": "Initial allocation",
    })
    assert budget.status_code == 200
    payload = mandate_payload()
    payload["allocation_weight"] = 0.6
    target_id = create_target(client)
    payload["benchmark_target_id"] = target_id
    created = client.post("/api/mandates/", json=payload)
    assert created.status_code == 201
    mandate = created.json()
    assert mandate["mandate_key"].startswith("mandate-")
    assert mandate["benchmark_target_name"] == "All Country"
    assert Decimal(mandate["budget_amount"]) == Decimal("6000000")

    assigned = client.put(
        f"/api/mandates/{mandate['mandate_id']}/investment-targets/{target_id}",
        json={"target_id": target_id, "target_weight": 0.5},
    )
    assert assigned.status_code == 200
    assert Decimal(assigned.json()[0]["target_amount"]) == Decimal("3000000")

    revised_budget = client.put("/api/mandates/capital-budget", json={
        "total_budget": "12000000", "currency": "JPY", "change_reason": "New deposit",
    })
    assert revised_budget.status_code == 200
    detail = client.get(f"/api/mandates/{mandate['mandate_id']}").json()
    assert Decimal(detail["budget_amount"]) == Decimal("7200000")
    assert Decimal(detail["assignments"][0]["target_amount"]) == Decimal("3600000")

    deleted = client.delete(f"/api/mandates/{mandate['mandate_id']}")
    assert deleted.status_code == 204
    assert client.get(f"/api/mandates/{mandate['mandate_id']}").status_code == 404


def test_mandate_allocation_sum_cannot_exceed_total_budget(client):
    payload = mandate_payload()
    payload["allocation_weight"] = 0.7
    assert client.post("/api/mandates/", json=payload).status_code == 201
    payload["mandate_name"] = "Second mandate"
    payload["allocation_weight"] = 0.4
    assert client.post("/api/mandates/", json=payload).status_code == 422


def test_mandate_rejects_absolute_budget_input(client):
    payload = mandate_payload()
    payload["budget_amount"] = "5000000"
    assert client.post("/api/mandates/", json=payload).status_code == 422

    payload.pop("budget_amount")
    created = client.post("/api/mandates/", json=payload)
    assert created.status_code == 201
    mandate_id = created.json()["mandate_id"]
    assert client.patch(f"/api/mandates/{mandate_id}", json={
        "budget_amount": "5000000", "change_reason": "Invalid absolute input",
    }).status_code == 422


# ---- 入力の上限 ----
#
# DB側はすべて上限なしのTEXTで、`currency` だけ長さのCHECKがある。つまりAPIが唯一の
# 関門である。さらに更新は mandate_version へ新しい行をINSERTして purpose や
# review_cycle_custom を引き継ぐため、1行の大きさが編集回数ぶん積み上がる。
#
# 公開デモでは誰でも書けるので、ここが抜けると保存量が素通りする。本文32KB上限と
# レート制限（main.py）はDoSを見るもので、保存される量は見ていない。

OVERSIZED = "あ" * 10_000


@pytest.mark.parametrize(
    "field",
    ["purpose", "mandate_name", "change_reason", "benchmark_security_key"],
)
def test_mandate_rejects_oversized_text(client, field):
    payload = mandate_payload()
    payload[field] = OVERSIZED

    assert client.post("/api/mandates/", json=payload).status_code == 422


def test_mandate_rejects_oversized_custom_review_cycle(client):
    """`review_cycle='other'` のときだけ必須になる自由記述も上限を持つ。"""
    payload = mandate_payload()
    payload["review_cycle"] = "other"
    payload["review_cycle_custom"] = OVERSIZED

    assert client.post("/api/mandates/", json=payload).status_code == 422


def test_mandate_rejects_absurd_expected_return(client):
    """桁を間違えた入力を受け付けない。年率の小数なので10（=1000%）が上限。"""
    payload = mandate_payload()
    payload["expected_return"] = 1e308

    assert client.post("/api/mandates/", json=payload).status_code == 422


@pytest.mark.parametrize("literal", ["Infinity", "-Infinity", "NaN"])
def test_mandate_rejects_non_finite_expected_return(client, literal):
    """`nan` が DOUBLE PRECISION に入ると集計が静かに壊れる。

    範囲のあるfloatは `inf` / `nan` を自動的に弾くが、無制限のfloatは通してしまう。
    準拠したクライアントはこれらをJSONへ書けないため、ボディを直接組んで送る。
    サーバ側は標準の `json` で読むので `Infinity` / `NaN` を受け取りうる。
    """
    payload = mandate_payload()
    payload.pop("expected_return")
    body = json.dumps(payload)[:-1] + f', "expected_return": {literal}}}'

    response = client.post(
        "/api/mandates/", content=body, headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 422


def test_mandate_accepts_realistic_values(client):
    """締めすぎていないことを確かめる。上限は実際の入力を拒まない。"""
    payload = mandate_payload()
    payload["expected_return"] = -0.3
    payload["review_cycle"] = "other"
    payload["review_cycle_custom"] = "隔週"

    assert client.post("/api/mandates/", json=payload).status_code == 201


def test_capital_budget_rejects_oversized_reason(client):
    """予算変更の理由も mandate 側の change_reason と同じ上限にする。"""
    response = client.put(
        "/api/mandates/capital-budget",
        json={"total_budget": "1000000", "currency": "JPY", "change_reason": OVERSIZED},
    )

    assert response.status_code == 422
