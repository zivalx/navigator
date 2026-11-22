"""Deploy-file invariants for docker-compose.prod.yml (no containers started)."""
from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.prod.yml"


def _redis():
    return yaml.safe_load(COMPOSE.read_text())["services"]["redis"]


def test_redis_password_in_container_env():
    # The ops restore script runs `REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli FLUSHDB`
    # inside the container, so the password must be in its environment.
    env = _redis()["environment"]
    names = set(env) if isinstance(env, dict) else {e.split("=", 1)[0] for e in env}
    assert "REDIS_PASSWORD" in names


def test_redis_healthcheck_keeps_password_out_of_argv():
    test = _redis()["healthcheck"]["test"]
    joined = " ".join(test)
    assert "-a" not in test and "--pass" not in joined
    assert "-a " not in joined
    assert "REDISCLI_AUTH" in joined
    assert "$$REDIS_PASSWORD" in joined  # compose-escaped: expanded in the container, not the host


def test_redis_env_value_is_interpolated_password():
    env = _redis()["environment"]
    assert env["REDIS_PASSWORD"] == "${REDIS_PASSWORD}"


def test_redis_command_reads_password_from_container_env():
    command = _redis()["command"]
    joined = command if isinstance(command, str) else " ".join(command)
    assert "${REDIS_PASSWORD}" not in joined  # would be interpolated into argv
    assert "$$REDIS_PASSWORD" in joined
