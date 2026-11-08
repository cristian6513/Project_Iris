"""Domain tests; the Store worker/lifecycle integration is in tests/test_app_extensions.py."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location('counter_backend', Path(__file__).parents[1] / 'backend/main.py')
backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backend)


def test_value_persists_and_invalid_amounts_leave_it_unchanged(tmp_path):
    context = SimpleNamespace(data_dir=tmp_path)
    assert backend.handle('status', {}, context) == {'count': 0}
    assert not list(tmp_path.iterdir()), 'reading is passive'
    assert backend.handle('increment', {'amount': 3}, context) == {'count': 3}
    assert backend.handle('status', {}, context) == {'count': 3}
    for amount in (0, -1, 1001):
        with pytest.raises(ValueError):
            backend.handle('increment', {'amount': amount}, context)
    assert backend.handle('status', {}, context) == {'count': 3}
    assert [path.name for path in tmp_path.iterdir()] == ['counter.json']
