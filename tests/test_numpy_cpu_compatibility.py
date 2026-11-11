"""Guard the NumPy wheel used on older x86-64 deployment hosts."""

import importlib
import platform

import numpy as np
import pytest


def test_numpy_x86_64_baseline_does_not_require_v2():
    if platform.machine().lower() not in {"x86_64", "amd64"}:
        pytest.skip("x86-64 CPU baseline does not apply")

    # NumPy 1.x calls this package core; 2.x calls it _core.
    core = "numpy._core" if int(np.__version__.split(".")[0]) >= 2 else "numpy.core"
    baseline = importlib.import_module(f"{core}._multiarray_umath").__cpu_baseline__
    assert set(baseline) <= {"SSE", "SSE2", "SSE3"}
    np.testing.assert_allclose(np.fft.irfft(np.fft.rfft([1.0, 0.0])), [1.0, 0.0])
