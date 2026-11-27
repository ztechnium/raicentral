"""Docker Compose, image and environment-template guarantees.

The Compose files are copied into a temporary directory before rendering, so a
developer's own ``.env`` never influences the result.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASE = "docker-compose.yml"
PROD = "docker-compose.prod.yml"
DEV = "docker-compose.dev.yml"
SERVICES = (
    "postgres",
    "redis",
    "rai-gateway",
    "rai-onboarding-worker",
    "control-plane-ui",
)
DEV_ENV = {"POSTGRES_PASSWORD": "local-password"}
PROD_ENV = {
    "POSTGRES_PASSWORD": "p-database",
    "JWT_SECRET_KEY": "j" * 64,
    "EVIDENCE_ENCRYPTION_KEY": "e" * 64,
    "APP_PUBLIC_URL": "https://rai.example.test",
    "CORS_ALLOWED_ORIGINS": "https://rai.example.test",
    "NEXT_PUBLIC_API_BASE_URL": "https://rai.example.test/api",
}


def _compose_available() -> bool:
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(
        ["docker", "compose", "version"], capture_output=True, check=False
    )
    return probe.returncode == 0


needs_compose = pytest.mark.skipif(
    not _compose_available(), reason="Docker Compose is not installed"
)


def _render(
    tmp_path: Path,
    env: dict[str, str],
    files: tuple[str, ...] = (BASE,),
) -> subprocess.CompletedProcess[str]:
    for name in files:
        shutil.copy(ROOT / name, tmp_path / name)
    lines = [f"{key}='{value}'" for key, value in env.items()]
    (tmp_path / ".env").write_text("\n".join(lines) + "\n", encoding="utf-8")
    command = ["docker", "compose", "--project-directory", str(tmp_path)]
    for name in files:
        command += ["-f", str(tmp_path / name)]
    command += ["config", "--format", "json"]
    # Only what Docker itself needs: host variables would override the test .env.
    keep = ("PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "APPDATA", "LOCALAPPDATA")
    clean = {k: v for k, v in os.environ.items() if k.upper() in keep}
    clean.update({k: v for k, v in os.environ.items() if k.startswith("DOCKER")})
    clean.update(
        {
            k: v
            for k, v in os.environ.items()
            if k.upper() in ("PROGRAMFILES", "PROGRAMDATA", "XDG_CONFIG_HOME")
        }
    )
    return subprocess.run(
        command,
        cwd=tmp_path,
        env=clean,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=120,
    )


def _config(tmp_path, env, files=(BASE,)) -> dict:
    result = _render(tmp_path, env, files)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)["services"]


def _ports(service: dict) -> list[tuple[str | None, str, int]]:
    return [
        (port.get("host_ip"), str(port.get("published")), port["target"])
        for port in service.get("ports", [])
    ]


@needs_compose
@pytest.mark.parametrize("files", [(BASE,), (BASE, PROD), (BASE, DEV)])
def test_compose_files_are_valid(tmp_path, files):
    services = _config(tmp_path, PROD_ENV, files)
    assert set(SERVICES) <= set(services)


@needs_compose
@pytest.mark.parametrize("env", [{}, {"POSTGRES_PASSWORD": ""}])
def test_postgres_password_is_required(tmp_path, env):
    result = _render(tmp_path, env)

    assert result.returncode != 0
    assert "POSTGRES_PASSWORD" in result.stderr


@needs_compose
def test_database_settings_match_and_need_no_url_encoding(tmp_path):
    password = "p@ss:w/rd#?%25 &=x"
    services = _config(tmp_path, {"POSTGRES_PASSWORD": password})

    assert services["postgres"]["environment"]["POSTGRES_PASSWORD"] == password
    for name in ("rai-gateway", "rai-onboarding-worker"):
        env = services[name]["environment"]
        assert env["DATABASE_URL"] == "postgresql://rai_user@postgres:5432/rai_gateway"
        assert env["PGPASSWORD"] == password


@needs_compose
def test_published_ports_default_to_loopback(tmp_path):
    services = _config(tmp_path, DEV_ENV)

    assert _ports(services["postgres"]) == [("127.0.0.1", "5434", 5432)]
    assert _ports(services["redis"]) == [("127.0.0.1", "6380", 6379)]
    assert _ports(services["rai-gateway"]) == [("127.0.0.1", "8001", 8000)]
    assert _ports(services["control-plane-ui"]) == [("127.0.0.1", "3000", 3000)]


@needs_compose
def test_external_binding_is_an_explicit_choice(tmp_path):
    services = _config(tmp_path, {**DEV_ENV, "GATEWAY_BIND_ADDRESS": "0.0.0.0"})

    assert _ports(services["rai-gateway"])[0][0] == "0.0.0.0"
    assert _ports(services["postgres"])[0][0] == "127.0.0.1"


@needs_compose
@pytest.mark.parametrize("extra", [{}, {"ADMIN_TOKEN": "", "STEP_UP_TOKEN": ""}])
@pytest.mark.parametrize("files", [(BASE,), (BASE, DEV)])
def test_admin_token_stays_empty(tmp_path, extra, files):
    services = _config(tmp_path, {**DEV_ENV, **extra}, files)
    env = services["rai-gateway"]["environment"]

    assert env["ADMIN_TOKEN"] == ""
    assert env["STEP_UP_TOKEN"] == ""
    assert env["ALLOW_SHARED_SECRET_STEP_UP"] == "false"
    assert env["ADMIN_BOOTSTRAP_ENABLED"] == "false"


@needs_compose
@pytest.mark.parametrize("files", [(BASE,), (BASE, DEV), (BASE, PROD)])
def test_frontend_never_receives_the_admin_token(tmp_path, files):
    secret = "backend-only-admin-token"
    env = {**PROD_ENV, "ADMIN_TOKEN": secret, "NEXT_PUBLIC_ADMIN_TOKEN": secret}
    ui = _config(tmp_path, env, files)["control-plane-ui"]

    assert secret not in json.dumps(ui)
    assert ui["environment"]["NEXT_PUBLIC_ADMIN_TOKEN"] == ""


@needs_compose
def test_production_override_closes_data_store_ports(tmp_path):
    services = _config(tmp_path, PROD_ENV, (BASE, PROD))

    assert _ports(services["postgres"]) == []
    assert _ports(services["redis"]) == []
    assert _ports(services["rai-gateway"])[0][0] == "127.0.0.1"
    assert _ports(services["control-plane-ui"])[0][0] == "127.0.0.1"


@needs_compose
def test_production_override_pins_development_switches_off(tmp_path):
    unsafe = {
        "ENVIRONMENT": "development",
        "ADMIN_TOKEN": "left-over",
        "STEP_UP_TOKEN": "left-over",
        "ADMIN_BOOTSTRAP_ENABLED": "true",
        "ADMIN_BOOTSTRAP_PASSWORD": "ChangeMe123!",
        "ALLOW_SHARED_SECRET_STEP_UP": "true",
        "AUTH_COOKIE_SECURE": "false",
        "MCP_ADAPTER_MODE": "DEMO_SIMULATED",
        "MTLS_ALLOW_INSECURE_IDENTITY_HEADER": "true",
    }
    services = _config(tmp_path, {**PROD_ENV, **unsafe}, (BASE, PROD))
    gateway = services["rai-gateway"]["environment"]

    assert gateway["ENVIRONMENT"] == "production"
    assert gateway["ADMIN_TOKEN"] == ""
    assert gateway["STEP_UP_TOKEN"] == ""
    assert gateway["ADMIN_BOOTSTRAP_ENABLED"] == "false"
    assert gateway["ADMIN_BOOTSTRAP_PASSWORD"] == ""
    assert gateway["ALLOW_SHARED_SECRET_STEP_UP"] == "false"
    assert gateway["AUTH_COOKIE_SECURE"] == "true"
    assert gateway["MCP_ADAPTER_MODE"] == "PRODUCTION"
    assert gateway["MTLS_ALLOW_INSECURE_IDENTITY_HEADER"] == "false"
    worker = services["rai-onboarding-worker"]["environment"]
    for key in ("ENVIRONMENT", "JWT_SECRET_KEY", "EVIDENCE_ENCRYPTION_KEY"):
        assert worker[key] == gateway[key]


@needs_compose
@pytest.mark.parametrize("missing", sorted(PROD_ENV))
def test_production_requires_secrets_and_public_urls(tmp_path, missing):
    env = {key: value for key, value in PROD_ENV.items() if key != missing}
    result = _render(tmp_path, env, (BASE, PROD))

    assert result.returncode != 0
    assert missing in result.stderr


@needs_compose
@pytest.mark.parametrize("files", [(BASE,), (BASE, PROD)])
def test_every_service_restarts_and_healthcheck_rejects_errors(tmp_path, files):
    services = _config(tmp_path, PROD_ENV, files)

    for name in SERVICES:
        assert services[name]["restart"] == "unless-stopped", name
    probe = " ".join(services["rai-gateway"]["healthcheck"]["test"])
    assert "raise_for_status()" in probe
    depends = services["rai-onboarding-worker"]["depends_on"]
    assert depends["rai-gateway"]["condition"] == "service_healthy"


def test_image_healthcheck_rejects_error_responses():
    assert "raise_for_status()" in (ROOT / "Dockerfile").read_text(encoding="utf-8")


def test_frontend_sources_do_not_read_an_admin_token():
    ui = ROOT / "control-plane-ui"
    dockerfile = (ui / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG NEXT_PUBLIC_ADMIN_TOKEN" not in dockerfile
    assert ".env.*" in (ui / ".dockerignore").read_text(encoding="utf-8")
    for folder in ("app", "components", "lib", "providers"):
        for source in (ui / folder).rglob("*.ts*"):
            assert "ADMIN_TOKEN" not in source.read_text(encoding="utf-8"), source


def _template(name: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in (ROOT / name).read_text(encoding="utf-8").splitlines():
        match = re.match(r"([A-Z][A-Z0-9_]*)=(.*)$", line)
        if match:
            values[match.group(1)] = match.group(2)
    return values


@pytest.mark.parametrize("name", [".env.example", ".env.production.example"])
def test_templates_ship_without_shared_admin_tokens(name):
    values = _template(name)

    assert values["ADMIN_TOKEN"] == ""
    assert values["NEXT_PUBLIC_ADMIN_TOKEN"] == ""
    assert values["STEP_UP_TOKEN"] == ""
    assert values["ALLOW_SHARED_SECRET_STEP_UP"] == "false"
    assert "POSTGRES_PASSWORD" in values


def test_production_template_leaves_secrets_for_the_operator():
    values = _template(".env.production.example")

    assert values["ADMIN_BOOTSTRAP_ENABLED"] == "false"
    for key in (
        "POSTGRES_PASSWORD",
        "JWT_SECRET_KEY",
        "EVIDENCE_ENCRYPTION_KEY",
        "ADMIN_BOOTSTRAP_PASSWORD",
        "SMTP_PASSWORD",
        "LLM_PROVIDER_API_KEY",
    ):
        assert values[key] == "", key


@needs_compose
def test_production_template_renders_once_secrets_are_filled(tmp_path):
    values = _template(".env.production.example")
    for key in ("POSTGRES_PASSWORD", "JWT_SECRET_KEY", "EVIDENCE_ENCRYPTION_KEY"):
        values[key] = "x" * 64
    values.pop("COMPOSE_FILE")

    assert _render(tmp_path, values, (BASE, PROD)).returncode == 0


def test_production_security_check_accepts_the_override(monkeypatch):
    """What docker-compose.prod.yml configures must pass startup validation."""
    from rai_gateway.core.config import settings
    from rai_gateway.core.security_config import validate_production_security

    for name, value in {
        "database_url": "postgresql://rai_user@postgres:5432/rai_gateway",
        "jwt_secret_key": "j" * 64,
        "evidence_encryption_key": "e" * 64,
        "admin_bootstrap_enabled": False,
        "admin_bootstrap_password": "",
        "admin_token": "",
        "allow_shared_secret_step_up": False,
        "auth_set_cookies": True,
        "auth_cookie_secure": True,
        "email_enabled": False,
        "mcp_adapter_mode": "PRODUCTION",
        "mtls_allow_insecure_identity_header": False,
        "mtls_trusted_proxy_mode": False,
        "cors_allowed_origins": "https://rai.example.test",
    }.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setenv("PGPASSWORD", "generated-secret")

    errors, warnings = validate_production_security()

    assert errors == []
    assert not any("database password" in warning for warning in warnings)


@pytest.mark.parametrize("password", ["", "ChangeMe123!"])
def test_production_security_check_rejects_weak_enabled_bootstrap(
    monkeypatch, password
):
    from rai_gateway.core.config import settings
    from rai_gateway.core.security_config import validate_production_security

    monkeypatch.setattr(settings, "admin_bootstrap_enabled", True)
    monkeypatch.setattr(settings, "admin_bootstrap_password", password)
    monkeypatch.setenv("PGPASSWORD", "rai_password")
    monkeypatch.setattr(
        settings, "database_url", "postgresql://rai_user@postgres:5432/rai_gateway"
    )

    errors, warnings = validate_production_security()

    assert any("bootstrap admin password" in error for error in errors)
    assert any("database password" in warning for warning in warnings)
