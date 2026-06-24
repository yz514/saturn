from saturn.analytics.forward import (
    _dcf,
    _solve_implied_growth,
    _solve_implied_return,
)


def test_dcf_matches_hand_computation():
    # fcf0=100, g=0, r=10%, n=2, terminal g_t=2.5%
    # PV = 100/1.1 + 100/1.21 + (100*1.025/0.075)/1.21
    #    = 90.909 + 82.645 + 1129.477 = 1303.03
    assert abs(_dcf(100.0, 0.0, 0.10, n=2, g_t=0.025) - 1303.03) < 0.1


def test_dcf_monotonic_in_discount_rate():
    # higher discount rate -> lower present value
    assert _dcf(100.0, 0.10, 0.08) > _dcf(100.0, 0.10, 0.10) > _dcf(100.0, 0.10, 0.12)


def test_solve_implied_growth_round_trips():
    target = _dcf(100.0, 0.12, 0.10)
    g, converged = _solve_implied_growth(100.0, target, 0.10)
    assert converged and abs(g - 0.12) < 1e-4


def test_solve_implied_growth_clamps_when_out_of_range():
    # an enormous target implies more growth than the +60% ceiling
    huge = _dcf(100.0, 0.60, 0.10) * 100
    g, converged = _solve_implied_growth(100.0, huge, 0.10)
    assert not converged and abs(g - 0.60) < 1e-9   # clamped to upper bound


def test_solve_implied_return_round_trips():
    target = _dcf(100.0, 0.05, 0.09)
    r = _solve_implied_return(100.0, 0.05, target)
    assert r is not None and abs(r - 0.09) < 1e-4
