.PHONY: up down test

up:
	docker compose up -d
	python scripts/wait_for_infra.py

down:
	docker compose down -v

test:
	ruff check . && pytest
