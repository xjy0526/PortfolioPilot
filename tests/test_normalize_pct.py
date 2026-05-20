"""PortfolioPilot - Tests für _normalize_pct() Edge Cases.

Stellt sicher, dass die Prozent-Normalisierung korrekt arbeitet:
  - Dezimalwerte (0.25 → 25%) werden erkannt und umgerechnet
  - Bereits in Prozent vorliegende Werte (25.0 → 25%) bleiben unverändert
  - Kleine Prozentwerte (3.0 = 3%) werden NICHT fälschlich als Dezimal interpretiert
"""
import pytest
from engine.scorer import _normalize_pct


class TestNormalizePct:
    """Tests für _normalize_pct()."""

    def test_none_returns_none(self):
        assert _normalize_pct(None) is None

    def test_decimal_to_percent(self):
        """0.25 sollte als Dezimalformat erkannt und zu 25.0 werden."""
        assert _normalize_pct(0.25) == pytest.approx(25.0)

    def test_small_decimal(self):
        """0.03 (= 3%) sollte korrekt zu 3.0 werden."""
        assert _normalize_pct(0.03) == pytest.approx(3.0)

    def test_small_percent_stays(self):
        """3.0 (= echte 3%) darf NICHT zu 300% werden."""
        assert _normalize_pct(3.0) == pytest.approx(3.0)

    def test_large_percent_stays(self):
        """25.0 (bereits in %) bleibt 25.0."""
        assert _normalize_pct(25.0) == pytest.approx(25.0)

    def test_negative_decimal(self):
        """-0.15 (= -15%) sollte korrekt zu -15.0 werden."""
        assert _normalize_pct(-0.15) == pytest.approx(-15.0)

    def test_negative_percent_stays(self):
        """-12.0 (bereits in %) bleibt -12.0."""
        assert _normalize_pct(-12.0) == pytest.approx(-12.0)

    def test_zero(self):
        """0 bleibt 0."""
        assert _normalize_pct(0) == 0

    def test_very_small_decimal(self):
        """0.005 (= 0.5%) sollte zu 0.5 werden."""
        assert _normalize_pct(0.005) == pytest.approx(0.5)

    def test_one_hundred_percent(self):
        """100.0 (= 100%) bleibt 100.0 (z.B. Wachstum 100%)."""
        assert _normalize_pct(100.0) == pytest.approx(100.0)

    def test_boundary_at_one(self):
        """0.99 (knapp unter 1.0) wird als Dezimal erkannt → 99%."""
        assert _normalize_pct(0.99) == pytest.approx(99.0)

    def test_exactly_one(self):
        """1.0 bleibt 1.0 (= 1%), wird NICHT als Dezimal behandelt."""
        assert _normalize_pct(1.0) == pytest.approx(1.0)

    def test_large_growth(self):
        """250.0 (= 250% Wachstum, z.B. NVDA) bleibt 250.0."""
        assert _normalize_pct(250.0) == pytest.approx(250.0)
