.PHONY: up down test load-checkout-model

up:
	docker compose up -d
	python scripts/wait_for_infra.py

down:
	docker compose down -v

test:
	ruff check . && pytest

load-checkout-model:
	python scripts/load_model.py --model-file contracts/examples/model_checkout.json
