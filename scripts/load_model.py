#!/usr/bin/env python3
"""CLI utility to load reference workflow models into Neo4j."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase

from services.model_builder.neo4j_loader import Neo4jModelLoader

log = logging.getLogger("straph.scripts.load_model")


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="Load reference workflow model JSON into Neo4j with schema constraints.",
    )
    parser.add_argument(
        "--model-file",
        required=True,
        type=Path,
        help="Path to reference model JSON file conforming to contracts/model.schema.json",
    )
    parser.add_argument(
        "--neo4j-uri",
        default=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        help="Neo4j connection URI (default: bolt://localhost:7687 or $NEO4J_URI)",
    )
    parser.add_argument(
        "--neo4j-user",
        default=os.getenv("NEO4J_USER", "neo4j"),
        help="Neo4j username (default: neo4j or $NEO4J_USER)",
    )
    parser.add_argument(
        "--neo4j-password",
        default=os.getenv("NEO4J_PASSWORD", ""),
        help="Neo4j password (default: $NEO4J_PASSWORD)",
    )
    return parser


def load_model_cli(
    model_file: Path,
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
    driver_instance: Any = None,
) -> int:
    """Execute model loading logic into Neo4j."""
    if not model_file.exists():
        print(f"Error: Model file not found at {model_file}", file=sys.stderr)
        return 1

    try:
        with open(model_file, "r", encoding="utf-8") as f:
            model_dict: dict[str, Any] = json.load(f)
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        print(f"Error: Failed to read or parse JSON model file: {exc}", file=sys.stderr)
        return 1

    driver = driver_instance
    owns_driver = False
    if driver is None:
        try:
            driver = GraphDatabase.driver(
                neo4j_uri,
                auth=(neo4j_user, neo4j_password),
            )
            owns_driver = True
        except (ConnectionError, OSError, RuntimeError) as exc:
            print(f"Error: Failed to connect to Neo4j at {neo4j_uri}: {exc}", file=sys.stderr)
            return 1

    try:
        loader = Neo4jModelLoader()
        with driver.session() as session:
            loader.apply_constraints(session)
            summary = loader.load_model(session, model_dict)

        res_type = model_dict.get("resource_type", "Unknown")
        print(
            f"Successfully loaded model for '{res_type}': "
            f"{summary['states_loaded']} states, "
            f"{summary['transitions_loaded']} transitions."
        )
        return 0
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"Error: Failed to load model into Neo4j: {exc}", file=sys.stderr)
        return 1
    finally:
        if owns_driver:
            try:
                driver.close()
            except (OSError, RuntimeError) as close_err:
                log.debug("Error closing driver: %s", close_err)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return load_model_cli(
        model_file=args.model_file,
        neo4j_uri=args.neo4j_uri,
        neo4j_user=args.neo4j_user,
        neo4j_password=args.neo4j_password,
    )


if __name__ == "__main__":
    sys.exit(main())
