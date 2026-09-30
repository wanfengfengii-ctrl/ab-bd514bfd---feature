"""Pytest fixtures and request builders."""
import pytest

from app.schemas import Exposure, Link, ScheduleRequest, SharedCooling


def exp(id, duration=2, earliest=0, latest=100, equipment="X", cooling=0,
        startup=0):
    return Exposure(
        id=id,
        duration=duration,
        earliest_start=earliest,
        latest_start=latest,
        equipment=equipment,
        cooling=cooling,
        startup_consumption=startup,
    )


def cooling(capacity, initial, recovery):
    return SharedCooling(capacity=capacity, initial=initial, recovery=recovery)


def make_request(exposures, links=None, horizon=1000, shared_cooling=None):
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


@pytest.fixture
def C():
    return cooling
