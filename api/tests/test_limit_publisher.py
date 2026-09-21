"""Testes da publicação periódica do limite (W4), incluindo *para quem* ela publica.

Decisão registrada na spec da W4: a tarefa só publica para equipamentos que **já se anunciaram**
(têm linha em `device_status`). `config` é retained num broker público, então publicar para o
catálogo inteiro deixaria mensagem órfã pendurada nos equipamentos que não têm firmware.
"""

import json
from collections.abc import Callable, Iterator
from datetime import date
from typing import Any

import httpx
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.clients.open_meteo import OpenMeteoClient
from app.core.clock import today_local
from app.models import PublishedConfig
from app.mqtt.publisher import known_device_ids, publish_limits_for_known_devices
from app.repositories.devices import set_status
from app.schemas.mqtt import StatusMessage
from app.services.terrain import clear_terrain_cache
from tests.conftest import FIXTURES_DIR
from tests.test_devices_limit_route import FakeBridge
from tests.test_risk_route import forecast_payload

DEVICE_ID = "tractor-01"
OTHER_DEVICE_ID = "harvester-01"

ClientFactory = Callable[..., OpenMeteoClient]


@pytest.fixture
def today() -> date:
    return today_local()


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture(autouse=True)
def clean_terrain() -> Iterator[None]:
    clear_terrain_cache()
    yield
    clear_terrain_cache()


@pytest.fixture
def open_meteo(make_client: ClientFactory, today: date) -> OpenMeteoClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if "elevation" in request.url.path:
            path = FIXTURES_DIR / "terrain_elevation_carmo.json"
            return httpx.Response(200, json=json.loads(path.read_text(encoding="utf-8")))
        return httpx.Response(200, json=forecast_payload(today))

    return make_client(handler)


@pytest.fixture
def failing_open_meteo(make_client: ClientFactory) -> OpenMeteoClient:
    return make_client(lambda _: httpx.Response(500, text="erro na origem"))


def announce(session: Session, device_id: str, state: str = "online") -> None:
    set_status(session, StatusMessage(device_id=device_id, state=state))


def test_nothing_is_published_before_any_device_announces_itself(
    session: Session, open_meteo: OpenMeteoClient
) -> None:
    """Sem isso, o broker público ficaria com `config` retained órfão em máquinas sem firmware."""
    bridge = FakeBridge()

    published = publish_limits_for_known_devices(bridge, open_meteo, session)

    assert published == []
    assert bridge.published == []


def test_a_device_that_announced_itself_gets_its_limit(
    session: Session, open_meteo: OpenMeteoClient
) -> None:
    announce(session, DEVICE_ID)
    bridge = FakeBridge()

    published = publish_limits_for_known_devices(bridge, open_meteo, session)

    assert published == [DEVICE_ID]
    assert bridge.published[0]["topic"].endswith(f"devices/{DEVICE_ID}/config")
    assert bridge.published[0]["retain"] is True
    assert "tilt_limit_deg" in bridge.published[0]["payload"]


def test_a_device_that_went_offline_still_gets_the_retained_config(
    session: Session, open_meteo: OpenMeteoClient
) -> None:
    """Ele já é conhecido: o retained fica esperando a próxima vez que ligar."""
    announce(session, DEVICE_ID, state="offline")
    bridge = FakeBridge()

    assert publish_limits_for_known_devices(bridge, open_meteo, session) == [DEVICE_ID]


def test_only_the_known_devices_receive(session: Session, open_meteo: OpenMeteoClient) -> None:
    announce(session, DEVICE_ID)
    bridge = FakeBridge()

    publish_limits_for_known_devices(bridge, open_meteo, session)

    topics = [sent["topic"] for sent in bridge.published]
    assert all(OTHER_DEVICE_ID not in topic for topic in topics)


def test_every_publication_is_recorded(session: Session, open_meteo: OpenMeteoClient) -> None:
    announce(session, DEVICE_ID)

    publish_limits_for_known_devices(FakeBridge(), open_meteo, session)

    rows = session.exec(select(PublishedConfig)).all()
    assert len(rows) == 1
    assert rows[0].device_id == DEVICE_ID


def test_a_disconnected_bridge_only_logs(session: Session, open_meteo: OpenMeteoClient) -> None:
    announce(session, DEVICE_ID)
    bridge = FakeBridge(connected=False)

    assert publish_limits_for_known_devices(bridge, open_meteo, session) == []
    assert bridge.published == []


def test_a_status_from_a_device_outside_the_catalogue_is_skipped(
    session: Session, open_meteo: OpenMeteoClient
) -> None:
    """O broker é público: alguém pode publicar status de um `device_id` que não é nosso."""
    announce(session, "trator-de-outra-pessoa")
    bridge = FakeBridge()

    assert publish_limits_for_known_devices(bridge, open_meteo, session) == []
    assert bridge.published == []


def test_open_meteo_down_does_not_kill_the_task(
    session: Session, failing_open_meteo: OpenMeteoClient
) -> None:
    """A tarefa roda em segundo plano: ela avisa no log e tenta de novo no ciclo seguinte."""
    announce(session, DEVICE_ID)
    bridge = FakeBridge()

    assert publish_limits_for_known_devices(bridge, failing_open_meteo, session) == []
    assert bridge.published == []


def test_a_broker_that_refuses_does_not_kill_the_task(
    session: Session, open_meteo: OpenMeteoClient
) -> None:
    announce(session, DEVICE_ID)

    assert (
        publish_limits_for_known_devices(
            FakeBridge(connected=True, accepts=False), open_meteo, session
        )
        == []
    )


def test_known_device_ids_lists_what_is_in_the_status_table(session: Session) -> None:
    announce(session, DEVICE_ID)
    announce(session, OTHER_DEVICE_ID, state="offline")

    assert known_device_ids(session) == {DEVICE_ID, OTHER_DEVICE_ID}


def test_publishing_for_a_specific_day(
    session: Session, open_meteo: OpenMeteoClient, today: date
) -> None:
    announce(session, DEVICE_ID)
    bridge: Any = FakeBridge()

    assert publish_limits_for_known_devices(bridge, open_meteo, session, today=today) == [DEVICE_ID]
