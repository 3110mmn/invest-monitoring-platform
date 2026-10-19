"""
テーマ CRUD API
"""
from fastapi import APIRouter, Depends, HTTPException

from app.database import Connection, get_db
from app.models.theme import (
    ThemeCreate,
    ThemeDetailRead,
    ThemeRead,
    ThemeSummaryRead,
    ThemeUpdate,
)
from app.repositories.theme_repository import ThemeRepository

router = APIRouter(prefix="/themes", tags=["themes"])


def _repo(conn: Connection = Depends(get_db)) -> ThemeRepository:
    return ThemeRepository(conn)


# ---- Theme ----

@router.get("/", response_model=list[ThemeRead])
def list_themes(is_active: bool | None = None, repo: ThemeRepository = Depends(_repo)):
    return repo.find_all(is_active=is_active)


@router.get("/summary", response_model=list[ThemeSummaryRead])
def theme_summary(
    include_archived: bool = False,
    repo: ThemeRepository = Depends(_repo),
):
    """テーマを構成銘柄数つきで返す。通常は有効Themeだけを対象にする。"""
    return repo.get_theme_summary(include_archived=include_archived)


@router.post("/", response_model=ThemeRead, status_code=201)
def create_theme(body: ThemeCreate, repo: ThemeRepository = Depends(_repo)):
    if body.theme_key and repo.find_by_key(body.theme_key):
        raise HTTPException(status_code=409, detail="theme_key already exists")
    if repo.find_active_by_name(body.theme_name):
        raise HTTPException(status_code=409, detail="同じ名称の有効なThemeが存在します")
    new_id = repo.create(body.model_dump())
    return repo.find_by_id(new_id)


@router.put("/{theme_id}", response_model=ThemeRead)
def update_theme(theme_id: int, body: ThemeUpdate, repo: ThemeRepository = Depends(_repo)):
    current = repo.find_by_id(theme_id)
    if not current:
        raise HTTPException(status_code=404, detail="Theme not found")
    changes = body.model_dump(exclude_none=True)
    resulting_name = changes.get("theme_name", current["theme_name"])
    resulting_active = changes.get("is_active", current["is_active"])
    if resulting_active and repo.find_active_by_name(
        resulting_name, exclude_theme_id=theme_id
    ):
        raise HTTPException(status_code=409, detail="同じ名称の有効なThemeが存在します")
    repo.update(theme_id, changes)
    return repo.find_by_id(theme_id)


@router.delete("/{theme_id}", status_code=204)
def delete_theme(theme_id: int, repo: ThemeRepository = Depends(_repo)):
    theme = repo.find_by_id(theme_id)
    if not theme:
        raise HTTPException(status_code=404, detail="Theme not found")
    dependencies = repo.deletion_dependencies(theme_id)
    if any(dependencies.values()):
        raise HTTPException(
            status_code=409,
            detail=(
                "構成銘柄の所属履歴またはTrigger設定から参照されているため完全削除できません。"
                "Themeをアーカイブして履歴を保管してください"
            ),
        )
    repo.hard_delete(theme_id)


@router.get("/{theme_id}", response_model=ThemeDetailRead)
def get_theme(theme_id: int, repo: ThemeRepository = Depends(_repo)):
    """テーマ1件を返す。"""
    theme = repo.find_detail_by_id(theme_id)
    if not theme:
        raise HTTPException(status_code=404, detail="Theme not found")
    return theme
