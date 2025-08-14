import os
import sys
import pytest

# Ensure project root is on sys.path for imports when running pytest from tests/
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app_utils import date_to_yyyymmdd, validate_pressure_level


def test_date_to_yyyymmdd_accepts_yyyymmdd():
    assert date_to_yyyymmdd('20250814') == '20250814'


def test_date_to_yyyymmdd_accepts_dash_format():
    assert date_to_yyyymmdd('2025-08-14') == '20250814'


@pytest.mark.parametrize('bad', ['', None, '2025/08/14', '2025081'])
def test_date_to_yyyymmdd_rejects_bad(bad):
    with pytest.raises(ValueError):
        date_to_yyyymmdd(bad)


def test_validate_pressure_level_accepts_int_and_str():
    assert validate_pressure_level(500) == 500
    assert validate_pressure_level('850') == 850


@pytest.mark.parametrize('bad', ['', None, 'abc', '12.3'])
def test_validate_pressure_level_rejects_bad(bad):
    with pytest.raises(ValueError):
        validate_pressure_level(bad)
