"""The native run itself receives termination, not merely the next clip loop."""
import threading
import time

import numpy as np
import pytest

from packages.config.errors import ContractError
from packages.encoders.onnx_cpu import FrozenOnnxEncoder


CLIP = np.zeros((1, 2, 3, 4, 4), dtype=np.float32)


class Options:
    terminate = False


class InterruptibleSession:
    def __init__(self):
        self.options = []
        self.entered = threading.Event()

    def run(self, outputs, inputs, run_options=None):
        self.options.append(run_options)
        self.entered.set()
        # Model ORT's documented terminate flag without an expensive native loop.
        until = time.monotonic() + 2
        while not run_options.terminate and time.monotonic() < until:
            self.entered.wait(.001)
            time.sleep(.001)
        if run_options.terminate:
            raise RuntimeError('ORT execution terminated')
        raise AssertionError('The native run did not receive termination')


def encoder(session):
    return FrozenOnnxEncoder('a' * 64, 3, 4, ('N', 'T', 3, 4, 4), session, Options)


def test_deadline_terminates_inflight_native_run_and_preserves_timeout():
    session = InterruptibleSession()
    started = time.monotonic()
    with pytest.raises(ContractError) as error:
        encoder(session).encode(CLIP, deadline=started + .06)
    assert error.value.code == 'ENCODER_TIMEOUT'
    assert session.options[0].terminate
    assert time.monotonic() - started < 1
    assert not any(t.name == 'jianwei-encoder-deadline' for t in threading.enumerate())


def test_cancel_interrupts_run_and_fresh_invocation_has_fresh_options():
    session = InterruptibleSession()

    def cancel_after_run_starts():
        if session.entered.is_set():
            raise ContractError('IMPORT_CANCELLED', 'cancelled')

    for _ in range(2):
        session.entered.clear()
        with pytest.raises(ContractError) as error:
            encoder(session).encode(CLIP, check_cancelled=cancel_after_run_starts)
        assert error.value.code == 'IMPORT_CANCELLED'
        assert session.options[-1].terminate
    assert session.options[0] is not session.options[1]
    assert not any(t.name == 'jianwei-encoder-deadline' for t in threading.enumerate())


def test_elapsed_deadline_does_not_enter_native_run():
    session = InterruptibleSession()
    with pytest.raises(ContractError) as error:
        encoder(session).encode(CLIP, deadline=time.monotonic() - 1)
    assert error.value.code == 'ENCODER_TIMEOUT'
    assert not session.entered.is_set()


def test_fast_result_that_exceeded_deadline_is_not_returned():
    class FinishingSession:
        def run(self, outputs, inputs, run_options=None):
            time.sleep(.015)
            return [np.zeros((1, 3), dtype=np.float32)]

    with pytest.raises(ContractError) as error:
        encoder(FinishingSession()).encode(CLIP, deadline=time.monotonic() + .005)
    assert error.value.code == 'ENCODER_TIMEOUT'


def test_bounded_real_ort_result_matches_legacy_call(tmp_path):
    onnx = pytest.importorskip('onnx')
    pytest.importorskip('onnxruntime')
    from tests.test_encoder import make_model
    from packages.encoders import load_encoder
    path = tmp_path / 'model.onnx'
    make_model(onnx, path)
    model = load_encoder(path, None, 3, 4, 1)
    calls = []
    bounded = model.encode(CLIP, deadline=time.monotonic() + 5, check_cancelled=lambda: calls.append(True))
    np.testing.assert_array_equal(bounded, model.encode(CLIP))
    assert calls
