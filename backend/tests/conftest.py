"""Pytest fixtures and request builders."""
import pytest

from app.schemas import Exposure, Link, ScheduleRequest, SharedCoolingConfig


def exp(id, duration=2, earliest=0, latest=100, equipment="X", cooling=0,
        startup_demand=None):
    return Exposure(
        id=id,
        duration=duration,
        earliest_start=earliest,
        latest_start=latest,
        equipment=equipment,
        cooling=cooling,
        startup_demand=startup_demand,
    )


def make_request(exposures, links=None, horizon=1000, shared_cooling=None):
    if shared_cooling is not None and not isinstance(
        shared_cooling, SharedCoolingConfig
    ):
        shared_cooling = SharedCoolingConfig(**shared_cooling)
    return ScheduleRequest(
        horizon=horizon,
        exposures=exposures,
        links=links or [],
        shared_cooling=shared_cooling,
    )


@pytest.fixture
def builder():
    return make_request


@pytest.fixture
def E():
    return exp
