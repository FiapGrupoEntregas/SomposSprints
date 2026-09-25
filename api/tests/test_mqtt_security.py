"""Guardrails TLS e de credenciais da conexão MQTT, sem broker real."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from app.core.config import Settings
from app.mqtt.bridge import MqttBridge, _build_mqtt_tls_context


def production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "environment": "production",
        "mqtt_enabled": True,
        "mqtt_host": "broker.prod.internal",
    }
    values.update(overrides)
    return Settings(**values)


def test_production_rejects_a_public_broker() -> None:
    with pytest.raises(ValueError, match="broker privado"):
        MqttBridge(
            production_settings(
                mqtt_host="broker.hivemq.com",
                mqtt_ca_cert="ca.pem",
                mqtt_username="api",
                mqtt_password="secret",
            ),
            client=Mock(),
        )


@pytest.mark.parametrize("host", ["broker.example.com", "8.8.8.8", "203.0.113.10", "127.0.0.1"])
def test_production_rejects_unverified_or_public_hosts(host: str) -> None:
    with pytest.raises(ValueError, match="broker privado"):
        _build_mqtt_tls_context(production_settings(mqtt_host=host))


def test_production_requires_a_ca_for_tls() -> None:
    with pytest.raises(ValueError, match="TLS com CA"):
        _build_mqtt_tls_context(production_settings())


def test_production_requires_broker_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ca_file = tmp_path / "ca.pem"
    ca_file.write_text("test CA", encoding="utf-8")
    monkeypatch.setattr("app.mqtt.bridge.ssl.create_default_context", lambda purpose: Mock())

    with pytest.raises(ValueError, match="credenciais do broker"):
        _build_mqtt_tls_context(production_settings(mqtt_ca_cert=str(ca_file)))


def test_invalid_tls_configuration_does_not_expose_certificate_path(tmp_path: Path) -> None:
    invalid_ca = tmp_path / "private-ca.pem"
    invalid_ca.write_text("not a valid certificate", encoding="utf-8")

    with pytest.raises(ValueError, match="TLS MQTT inválida") as error:
        _build_mqtt_tls_context(
            Settings(environment="dev", mqtt_enabled=True, mqtt_ca_cert=str(invalid_ca))
        )

    assert str(invalid_ca) not in str(error.value)


def test_client_uses_validated_tls_and_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    ca_file = tmp_path / "ca.pem"
    ca_file.write_text("test CA", encoding="utf-8")
    context = Mock()
    monkeypatch.setattr("app.mqtt.bridge.ssl.create_default_context", lambda purpose: context)
    client = Mock()
    settings = production_settings(
        mqtt_ca_cert=str(ca_file),
        mqtt_username="api-user",
        mqtt_password="secret-value",
    )

    MqttBridge(settings, client=client)

    client.tls_set_context.assert_called_once_with(context)
    client.username_pw_set.assert_called_once_with("api-user", "secret-value")
    assert "secret-value" not in caplog.text


def test_credentials_cannot_be_configured_without_tls() -> None:
    with pytest.raises(ValueError, match="sem TLS"):
        _build_mqtt_tls_context(
            Settings(
                environment="dev",
                mqtt_enabled=True,
                mqtt_username="api-user",
                mqtt_password="secret-value",
            )
        )


def test_settings_repr_does_not_expose_credentials() -> None:
    settings = Settings(
        environment="dev",
        api_keys="api-secret-value",
        mqtt_password="mqtt-secret-value",
    )

    assert "api-secret-value" not in repr(settings)
    assert "mqtt-secret-value" not in repr(settings)


def test_client_certificate_and_key_must_be_paired() -> None:
    with pytest.raises(ValueError, match="certificado e chave"):
        _build_mqtt_tls_context(
            Settings(environment="dev", mqtt_enabled=True, mqtt_client_cert="client.pem")
        )


def test_disabled_mqtt_does_not_require_production_credentials() -> None:
    assert (
        _build_mqtt_tls_context(
            Settings(environment="production", mqtt_enabled=False, mqtt_host="broker.hivemq.com")
        )
        is None
    )
