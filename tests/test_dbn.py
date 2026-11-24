"""DBN loader: instrument-id/date mapping check (ids can be reused over time)."""
from datetime import date

import pytest

from liqengine.dbn import check_mapping

MAP = [{"start_date": date(2025, 1, 1), "end_date": date(2025, 3, 1), "symbol": "8071"},
       {"start_date": date(2025, 3, 1), "end_date": date(2025, 6, 1), "symbol": "9001"}]


@pytest.mark.parametrize("iid,d,res", [
    (8071, date(2025, 1, 1), "ok"),
    (8071, date(2025, 2, 28), "ok"),
    (8071, date(2025, 3, 1), "ok_end_inclusive"),   # reported separately, convention unverified
    (9001, date(2025, 3, 1), "ok"),
    (8071, date(2025, 4, 1), "mismatch"),            # id not valid for this symbol on that date
    (1234, date(2025, 1, 5), "mismatch"),
])
def test_check_mapping(iid, d, res):
    assert check_mapping(MAP, iid, d) == res


def test_real_file_roundtrip_if_available():
    pytest.importorskip("databento_dbn")
    pytest.importorskip("zstandard")
    import glob, os
    files = [f for f in glob.glob(os.environ.get("LIQ_DBN_FIXTURE", "/nonexistent/*.dbn.zst"))
             if "-" not in f.split(".")[-3]]  # outrights only; spreads use another grid
    if not files:
        pytest.skip("no DBN fixture (set LIQ_DBN_FIXTURE)")
    from decimal import Decimal
    from liqengine.dbn import load_databento_dbn
    from liqengine.instrument import Contract, InstrumentConfig
    f = files[0]
    sym = f.split(".")[-3]
    inst = InstrumentConfig("MNQ", True, Decimal("0.25"), "X", None,
                            [Contract(sym, None, 10**12, Decimal("0.25"), True)])
    st, rep = load_databento_dbn(f, inst)
    assert rep.rows == len(st.bars.get(sym, {})) + rep.identical_cross_file_duplicates
