import json

import pytest

from photosorter.synth import generate


@pytest.fixture(scope="session")
def synth(tmp_path_factory):
    d = tmp_path_factory.mktemp("synth")
    generate(d / "data")
    data = d / "data"
    return data, json.loads((data / "truth.json").read_text())
