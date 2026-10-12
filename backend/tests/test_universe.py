"""
Tests for the universe tools (autoportfolio.universe_for) — synthetic data only.

FINDING: there is NO point-in-time universe construction in the codebase.
`autoportfolio.universe_for(book)` takes no as-of date: it returns the static
MEGACAP_CORE list (today's constituents) plus the *live* Congress tally. So the
survivorship / lookahead properties cannot hold and those tests are xfail(strict)
against the real code. They flip to XPASS->failure once an as-of API exists.

Each property is also checked on a test-local reference oracle (`pit_universe`)
and compared with the placebo (`naive_universe` = "today's constituents"), to
prove the tests can detect the bias they target.
"""
import os, sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import autoportfolio as ap  # noqa: E402

# ---- synthetic history: ticker -> (listed_on, delisted_on or None); ISO dates sort lexically
LISTINGS = {
    "OLDCO":  ("2010-01-01", None),          # always there
    "DEADCO": ("2012-01-01", "2020-06-30"),  # delisted before T
    "NEWCO":  ("2023-01-01", None),          # listed after T
    "LATE":   ("2022-06-01", None),          # listed after T
    "GONE":   ("2015-01-01", "2021-12-31"),  # delisted before T
    "ALIVE":  ("2018-01-01", "2024-12-31"),  # alive at T, delisted later
}
T = "2022-01-03"


def pit_universe(listings, t):
    """Reference oracle: listed on/before t and not delisted before t."""
    return sorted({k for k, (a, d) in listings.items() if a <= t and (d is None or d >= t)})


def naive_universe(listings, today="2026-10-12"):
    """Placebo: today's constituents regardless of t."""
    return pit_universe(listings, today)


# ---------------- reference oracle + placebo ----------------
def test_oracle_pit_has_no_survivorship():
    u = pit_universe(LISTINGS, T)
    assert u == ["ALIVE", "OLDCO"]
    assert "DEADCO" not in u and "GONE" not in u          # delisted before T excluded
    assert "NEWCO" not in u and "LATE" not in u           # not yet listed excluded


def test_placebo_naive_differs_from_pit():
    naive, pit = naive_universe(LISTINGS), pit_universe(LISTINGS, T)
    assert naive != pit
    assert "NEWCO" in naive and "NEWCO" not in pit        # lookahead
    assert "ALIVE" not in naive and "ALIVE" in pit        # survivorship (dropped loser)


def test_oracle_invariant_to_future_perturbation():
    base = pit_universe(LISTINGS, T)
    pert = dict(LISTINGS)
    pert["ALIVE"] = ("2018-01-01", "2022-01-04")          # delisting after T moved
    pert["NEWCO"] = ("2030-01-01", None)
    pert["FUTURE"] = ("2025-01-01", None)
    assert pit_universe(pert, T) == base
    # placebo reacts to the same perturbation -> shows the test has power
    assert naive_universe(pert) != naive_universe(LISTINGS)


def test_oracle_deterministic_no_dups():
    runs = [pit_universe(LISTINGS, T) for _ in range(5)]
    assert all(r == runs[0] for r in runs)
    assert len(runs[0]) == len(set(runs[0]))


# ---------------- real code: universe_for ----------------
@pytest.fixture
def synth(monkeypatch):
    """Point universe_for at synthetic inputs (no network)."""
    monkeypatch.setattr(ap, "MEGACAP_CORE", ["OLDCO", "NEWCO", "ALIVE"])
    monkeypatch.setattr(ap, "_congress_buys", lambda: ["NEWCO", "LATE", "OLDCO"])


def test_real_universe_deterministic_sorted_no_dups(synth):
    runs = [ap.universe_for("core") for _ in range(5)]
    assert all(r == runs[0] for r in runs)
    assert len(runs[0]) == len(set(runs[0]))               # overlap core/congress deduped
    assert runs[0] == sorted(runs[0])
    assert ap.universe_for("congress") == ["LATE", "NEWCO", "OLDCO"]


def test_real_universe_is_the_naive_placebo(synth):
    """Documents the bug: today's list == placebo, != point-in-time at T."""
    real = ap.universe_for("core")
    assert real != pit_universe(LISTINGS, T)
    assert "NEWCO" in real and "LATE" in real              # not listed until after T


def test_megacap_core_has_no_duplicates():
    # universe_for's set() would mask duplicates, so check the source list itself
    assert len(ap.MEGACAP_CORE) == len(set(ap.MEGACAP_CORE))
    assert all(t == t.upper() for t in ap.MEGACAP_CORE)


BUG = ("universe_for() has no as-of date: static MEGACAP_CORE (today's constituents) "
       "+ live Congress tally => survivorship and lookahead bias in any historical use")


@pytest.mark.xfail(strict=True, reason=BUG)
def test_real_universe_point_in_time_membership(synth):
    u = ap.universe_for("core", asof=T)
    assert set(u).isdisjoint({"NEWCO", "LATE", "DEADCO", "GONE"})
    assert "ALIVE" in u


@pytest.mark.xfail(strict=True, reason=BUG)
def test_real_universe_unchanged_by_future_data(synth, monkeypatch):
    before = ap.universe_for("core", asof=T)
    monkeypatch.setattr(ap, "_congress_buys", lambda: ["ZZZ_FUTURE"])  # post-T perturbation
    assert ap.universe_for("core", asof=T) == before
