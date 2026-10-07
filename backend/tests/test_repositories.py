from app.repositories.investment_target_repository import InvestmentTargetRepository
from app.repositories.theme_repository import ThemeRepository


def test_theme_repository_filters_active_rows(db):
    repo = ThemeRepository(db)
    active_id = repo.create({"theme_key": "active", "theme_name": "Active"})
    inactive_id = repo.create({"theme_key": "inactive", "theme_name": "Inactive"})
    repo.soft_delete(inactive_id)

    assert [row["theme_id"] for row in repo.find_all(is_active=True)] == [active_id]


def test_investment_target_repository_creates_timestamped_row(db):
    repo = InvestmentTargetRepository(db)
    target_id = repo.create({"target_key": "SPY", "target_name": "S&P 500", "target_type": "etf"})
    investment_target = repo.find_by_id(target_id)

    assert investment_target["created_at"]
    assert investment_target["updated_at"]
