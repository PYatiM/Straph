"""Unit tests for the scripts/load_model.py CLI utility."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.load_model import build_parser, load_model_cli, main
from services.model_builder.neo4j_loader import InMemoryGraphDriver

ROOT_DIR = Path(__file__).resolve().parents[2]
CHECKOUT_MODEL_PATH = ROOT_DIR / "contracts" / "examples" / "model_checkout.json"


def test_build_parser_defaults() -> None:
    """Verify CLI argument parser configuration."""
    parser = build_parser()
    args = parser.parse_args(["--model-file", str(CHECKOUT_MODEL_PATH)])
    assert args.model_file == CHECKOUT_MODEL_PATH
    assert args.neo4j_uri == "bolt://localhost:7687"
    assert args.neo4j_user == "neo4j"
    assert args.neo4j_password == ""


def test_load_model_cli_success_with_mock_driver(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify successful execution returns code 0 and prints loaded summary."""
    mock_driver = InMemoryGraphDriver()

    exit_code = load_model_cli(
        model_file=CHECKOUT_MODEL_PATH,
        neo4j_uri="bolt://localhost:7687",
        neo4j_user="neo4j",
        neo4j_password="",
        driver_instance=mock_driver,
    )

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Successfully loaded model for 'Order': 10 states, 10 transitions." in captured.out
    assert len(mock_driver.get_states()) == 10
    assert len(mock_driver.get_transitions()) == 10


def test_load_model_cli_missing_file(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify missing file returns code 1."""
    missing_path = Path("contracts/examples/nonexistent_model.json")
    exit_code = load_model_cli(
        model_file=missing_path,
        neo4j_uri="bolt://localhost:7687",
        neo4j_user="neo4j",
        neo4j_password="",
    )
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Model file not found" in captured.err


def test_load_model_cli_invalid_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Verify malformed JSON file returns code 1."""
    broken_file = tmp_path / "broken.json"
    broken_file.write_text("not json", encoding="utf-8")

    exit_code = load_model_cli(
        model_file=broken_file,
        neo4j_uri="bolt://localhost:7687",
        neo4j_user="neo4j",
        neo4j_password="",
    )
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Failed to read or parse JSON model file" in captured.err


def test_main_cli_entrypoint_with_mock_neo4j(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify main() entrypoint parses arguments and executes successfully."""
    mock_driver = InMemoryGraphDriver()

    with patch("scripts.load_model.GraphDatabase.driver", return_value=mock_driver):
        exit_code = main(["--model-file", str(CHECKOUT_MODEL_PATH)])

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "Successfully loaded model for 'Order'" in captured.out


def test_main_cli_connection_error(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify main() handles database connection errors gracefully."""
    with patch(
        "scripts.load_model.GraphDatabase.driver",
        side_effect=ConnectionError("Cannot connect to Neo4j"),
    ):
        exit_code = main(["--model-file", str(CHECKOUT_MODEL_PATH)])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Failed to connect to Neo4j" in captured.err


def test_main_cli_loader_exception(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify loader exceptions return exit code 1."""
    failing_driver = MagicMock()
    failing_driver.session.side_effect = RuntimeError("Cypher execution failed")

    with patch("scripts.load_model.GraphDatabase.driver", return_value=failing_driver):
        exit_code = main(["--model-file", str(CHECKOUT_MODEL_PATH)])

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "Failed to load model into Neo4j" in captured.err
