"""pytest entry point: each offline suite of tests/suites runs as one test, in its own process
(the suites patch modules and the environment globally). Run: pytest -q   (or pytest -k stv)"""
import pytest

from run_all import SKIPPED, SUITES, run_suite


@pytest.mark.parametrize("suite", SUITES, ids=[s.stem.removeprefix("suite_") for s in SUITES])
def test_suite(suite):
    code, out, _ = run_suite(suite)
    if code == SKIPPED:
        pytest.skip(next((line for line in out.splitlines() if line.startswith("SKIPPED")), "skipped"))
    if code != 0:
        failures = [line for line in out.splitlines() if line.startswith(("FAIL", "Traceback"))]
        pytest.fail("\n".join(failures[:40]) + "\n\n--- last lines ---\n" + "\n".join(out.splitlines()[-40:]),
                    pytrace=False)
