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


ROOT = Path(__file__).resolve().parents[2]
DOCKERFILES = [ROOT / "backend" / "Dockerfile.prod", ROOT / "backend" / "Dockerfile", ROOT / "frontend" / "Dockerfile"]


def _instructions(path):
    return [ln for ln in path.read_text().splitlines() if not ln.lstrip().startswith("#")]


def test_download_runs_mount_optional_corp_ca_secret():
    for path in DOCKERFILES:
        blocks = [b for b in path.read_text().split("\nRUN ")[1:] if "pip install" in b or "npm ci" in b]
        assert len(blocks) == 1, path
        head = blocks[0].split("pip install")[0].split("npm ci")[0]
        assert "--mount=type=secret,id=corp_ca,required=false" in head, path
        assert "/run/secrets/corp_ca" in head, path


def _joined_instructions(path):
    """Instructions with backslash-continuation lines joined into one logical line."""
    out, cur = [], ""
    for ln in _instructions(path):
        if cur:
            cur += " " + ln.strip()
        else:
            cur = ln
        if cur.endswith("\\"):
            cur = cur[:-1].rstrip()
            continue
        out.append(cur)
        cur = ""
    if cur:
        out.append(cur)
    return out


def test_pip_blocks_build_bundle_from_system_roots_and_clean_up():
    for path in DOCKERFILES[:2]:
        text = path.read_text()
        assert "ca-certificates.crt" in text, path
        assert "rm -f /tmp/ca-bundle.pem" in text, path
        assert "|| exit 1" in text, path


def test_frontend_npm_uses_node_extra_ca_certs():
    text = (ROOT / "frontend" / "Dockerfile").read_text()
    assert "NODE_EXTRA_CA_CERTS=/run/secrets/corp_ca" in text


def test_no_system_trust_store_modification():
    for path in DOCKERFILES:
        text = path.read_text()
        assert "update-ca-certificates" not in text, path
        assert "/usr/local/share/ca-certificates" not in text, path


def test_no_cert_copied_or_in_env():
    import re

    for path in DOCKERFILES:
        for ln in _joined_instructions(path):
            if re.match(r"\s*(COPY|ADD)\b", ln):
                assert not re.search(r"\.(pem|crt|cer)\b|corp_ca", ln), (path, ln)
            if re.match(r"\s*(ENV|ARG)\b", ln):
                assert not re.search(r"corp_ca|SSL_CERT|PIP_CERT|CA_CERTS|\.pem", ln, re.I), (path, ln)


def test_build_script_passes_secret_only_when_corp_ca_set():
    text = (ROOT / "scripts" / "build-images.sh").read_text()
    assert text.count("--secret") == 2
    _, rest = text.split('if [ -n "${CORP_CA:-}" ]; then', 1)
    branch, other = rest.split("else", 1)
    assert branch.count("--secret id=corp_ca,src=\"$CORP_CA\"") == 2
    assert "--secret" not in other


def test_deploy_script_does_not_use_corp_ca():
    assert "corp_ca" not in (ROOT / "scripts" / "deploy.sh").read_text().lower()


ENV_PROD_EXAMPLE = COMPOSE.parent / ".env.prod.example"
# Settings an operator never sets in .env.prod: scheduler_enabled is a switch for scripts
# and laptops sharing a DB, and production must keep its default (true).
_NOT_IN_PROD_TEMPLATE = {"SCHEDULER_ENABLED"}


def test_env_prod_example_lists_every_setting():
    # .env.prod is filled in from this template, so a setting missing here is a setting
    # nobody sets in production (e.g. Telegram, which silently leaves alerts in-app only).
    import re

    from app.config import Settings

    listed = set(re.findall(r"^([A-Z0-9_]+)=", ENV_PROD_EXAMPLE.read_text(), re.M))
    expected = {name.upper() for name in Settings.model_fields} - _NOT_IN_PROD_TEMPLATE
    assert sorted(expected - listed) == []
