BACKEND_IMAGE ?= invest-monitoring-api:local
FRONTEND_IMAGE ?= invest-monitoring-frontend:local
DATABASE_URL ?= postgresql://invest:invest@localhost:5432/invest

.PHONY: db-up db-down db-migrate db-test-create db-provision-roles docker-build docker-run frontend-docker-build frontend-docker-run research-pbr

RESEARCH_LAKE ?= data/parquet

db-up:
	docker compose up -d postgres

db-down:
	docker compose down

db-migrate:
	DATABASE_URL="$(DATABASE_URL)" venv/bin/alembic -c backend/alembic.ini upgrade head

# テストは全テーブルをTRUNCATEするため、開発DBとは別のDBを使う。
# compose の初回起動時にも作られるが、既存ボリュームには無いのでこれで追加する。
db-test-create:
	DATABASE_URL="$(DATABASE_URL)" venv/bin/python backend/db/create_test_database.py

db-provision-roles:
	cd backend && ../venv/bin/python -m db.provision_roles --apply

docker-build:
	docker build --file backend/Dockerfile --tag "$(BACKEND_IMAGE)" .

docker-run:
	docker run --rm --publish 8000:8080 \
		--env 'CORS_ORIGINS=["http://localhost:3000"]' \
		--env DATABASE_URL="postgresql://invest:invest@host.docker.internal:5432/invest" \
		"$(BACKEND_IMAGE)"

frontend-docker-build:
	docker build --file frontend/Dockerfile \
		--build-arg NEXT_PUBLIC_API_URL="http://localhost:8000/api" \
		--build-arg NEXT_PUBLIC_READ_ONLY=false \
		--tag "$(FRONTEND_IMAGE)" .

frontend-docker-run:
	docker run --rm --publish 3000:8080 "$(FRONTEND_IMAGE)"

