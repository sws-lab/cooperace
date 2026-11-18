# SPDX-FileCopyrightText: 2024-2026 University of Tartu
#
# SPDX-License-Identifier: MIT

"""The build's guard on the conf an archive runs (scripts/sv-comp/submission-conf.py,
scripts/svcomp-dist.sh): only a conf in the first-verdict mode in every
property is packed without ALLOW_BRACKET_CONF=1, and a build with CONF_VARIANT
unset or "default" is refused before anything is built."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.cooperace import config
from src.cooperace.components import REGISTRY
from src.cooperace.properties import FORMULAS

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "sv-comp" / "submission-conf.py"
CANDIDATES = ["fv-final-nosaf", "fv-final-saf", "fv-final-saf-readmit", "fv-final-saf-iekke",
              "fv-final-saf-readmit-iekke"]


def submission_conf():
    spec = importlib.util.spec_from_file_location("submission_conf", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def problems_of(path):
    strategies = config.load_strategies(json.loads(path.read_text()), REGISTRY)
    return submission_conf().problems(strategies, FORMULAS)


@pytest.mark.parametrize("variant", CANDIDATES)
def test_every_candidate_conf_is_a_submission_conf(variant):
    assert problems_of(ROOT / "conf" / f"svcomp27-{variant}.json") == []


def test_the_bracket_conf_is_no_submission_conf():
    found = problems_of(ROOT / "conf" / "svcomp27.json")

    assert {f"the strategy for {name} is not in the first-verdict mode (\"firstVerdict\": true)"
            for name in FORMULAS} <= set(found)
    assert "the strategy for valid-memsafety has vetoWindows" in found


def test_a_conf_without_a_strategy_for_a_property_is_no_submission_conf():
    conf = json.loads((ROOT / "conf" / "svcomp27-fv-final-saf.json").read_text())
    del conf["properties"]["valid-memsafety"]

    assert submission_conf().problems(config.load_strategies(conf, REGISTRY), FORMULAS) == [
        "no strategy for valid-memsafety"]


@pytest.mark.parametrize("conf, status", [("svcomp27.json", 1), ("svcomp27-fv-final-saf.json", 0)])
def test_the_script_exits_1_for_a_bracket_conf_and_0_for_a_submission_conf(conf, status):
    done = subprocess.run([sys.executable, str(SCRIPT), str(ROOT), str(ROOT / "conf" / conf)],
                          capture_output=True, text=True, timeout=60, check=False)

    assert done.returncode == status, done.stderr
    assert (done.stderr == "") == (status == 0)


@pytest.mark.parametrize("variant", [None, "default"])
def test_a_build_without_a_variant_or_with_default_is_refused_before_anything_is_built(variant):
    env = {key: value for key, value in os.environ.items() if key not in ("CONF_VARIANT", "ALLOW_BRACKET_CONF")}
    if variant is not None:
        env["CONF_VARIANT"] = variant

    done = subprocess.run(["bash", str(ROOT / "scripts" / "svcomp-dist.sh")], env=env, capture_output=True,
                          text=True, timeout=60, check=False)

    assert done.returncode == 1
    assert done.stderr.startswith("Refusing to build: CONF_VARIANT is unset or default")
    assert "ALLOW_BRACKET_CONF=1" in done.stderr
