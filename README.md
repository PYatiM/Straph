# Straph
Graph-based IDS for multi-step authorization and business workflow abuse. Design-stage repository scaffold:
shared contracts in `/contracts`, one folder per service under `/services`, contract tests in `/tests`.
Stack (proposed): Python, FastAPI, Kafka, Redis, Neo4j. Pin all versions in the Compose file when infrastructure lands (task T02).
Run checks: `ruff check . && pytest`
