"""Frontend guards for the PostgreSQL research workspace and valuation semantics."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
WORKSPACE_JS = (ROOT / "static" / "research-workspace.js").read_text(encoding="utf-8")
STYLES = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")


def test_research_workspace_is_a_first_class_navigation_view() -> None:
    assert 'data-tab="research"' in INDEX
    assert 'id="tab-research"' in INDEX
    assert 'id="corePortfolioSelect"' in INDEX
    assert 'id="coreResearchWorkspace"' in INDEX
    assert '/static/research-workspace.js?v=2' in INDEX


def test_workspace_reads_postgres_core_endpoints_and_effective_activity() -> None:
    expected_paths = (
        "/api/portfolios",
        "/api/portfolios/${id}/valuation",
        "/api/portfolios/${id}/positions",
        "/api/portfolios/${id}/transactions",
        "/api/portfolio/risk-summary?portfolio_id=${id}",
    )
    for path in expected_paths:
        assert path in WORKSPACE_JS

    assert "/transactions/audit" not in WORKSPACE_JS
    assert "superseded legacy generations" in WORKSPACE_JS


def test_snapshot_base_currency_is_not_reinterpreted_as_eur() -> None:
    assert "syncPortfolioDisplayCurrency(nextPortfolioData)" in APP_JS
    assert "if (portfolioData?.display_currency) return 1;" in APP_JS
    assert "Amounts are fixed to the traceable snapshot base currency" in APP_JS
    assert "Backend values remain EUR-based" not in APP_JS


def test_unavailable_valuation_has_bounded_retry_and_fail_closed_copy() -> None:
    assert "portfolioLoadAttempts < 3" in APP_JS
    assert "portfolio_rebuild_required" in APP_JS
    assert "stale snapshot was rejected" in APP_JS
    assert 'id="portfolioLoadState"' in INDEX


def test_research_tab_uses_one_portfolio_context_and_avoids_duplicate_hero() -> None:
    assert "setActivePortfolioId(state.selectedPortfolioId)" in WORKSPACE_JS
    assert "portfolioScopedUrl('/api/portfolio')" in APP_JS
    assert "document.body.dataset.activeTab = tab" in APP_JS
    assert 'body[data-active-tab="research"] .v2-balance-card' in STYLES


def test_analysis_and_legacy_writes_follow_the_selected_portfolio() -> None:
    scoped_paths = (
        "portfolioScopedUrl('/api/portfolio/risk-summary')",
        "portfolioScopedUrl('/api/portfolio/rebalance')",
        "portfolioScopedUrl('/api/portfolio/upload-csv')",
        "portfolioScopedUrl('/api/portfolio/csv-positions')",
        "portfolioScopedUrl(url)",
    )
    for path in scoped_paths:
        assert path in APP_JS

    assert "portfolioScopedUrl(endpoint)" in APP_JS
    assert "? '/api/portfolio/risk-summary'" in APP_JS
    assert ": '/api/risk'" in APP_JS
    assert "portfolio_id: activePortfolioId || null" in APP_JS


def test_history_view_uses_postgres_snapshots_and_effective_activity() -> None:
    assert 'id="coreHistoryWorkspace"' in INDEX
    assert "portfolioScopedUrl('/api/portfolio/history?days=365')" in APP_JS
    assert "portfolioScopedUrl('/api/portfolio/activities')" in APP_JS
    assert "await loadCoreHistory();" in APP_JS
    assert "Ordinary activity excludes superseded legacy generations" in APP_JS
    assert "no synthetic return curve or performance metric is shown" in APP_JS


def test_unbound_legacy_analytics_are_not_presented_as_current_portfolio_data() -> None:
    assert "legacy benchmark endpoint is not bound" in APP_JS
    assert "not yet PostgreSQL-backed" in APP_JS
    assert "portfolio_snapshot_id" in APP_JS
    assert "not bound to the active valuation snapshot" in APP_JS


def test_risk_portrait_and_summary_share_the_deterministic_risk_source() -> None:
    assert "isFundResearchMode()\n            ? '/api/portfolio/risk-summary'" in APP_JS
    assert "renderDeterministicRiskProfile(data);" in APP_JS
    assert "riskMetricStatusLabel(metricStatus.annual_volatility)" in APP_JS
    assert "insufficient data" in APP_JS


def test_mobile_layout_removes_desktop_sidebar_and_closed_slide_panel() -> None:
    mobile_start = STYLES.rfind("@media (max-width: 768px)")
    mobile_styles = STYLES[mobile_start:]
    assert ".app-sidebar" in mobile_styles
    assert "display: none" in mobile_styles
    assert ".app-main-content" in mobile_styles
    assert "margin-left: 0" in mobile_styles
    assert ".slide-panel:not(.open)" in mobile_styles
    assert 'id="stockPanel" aria-hidden="true"' in INDEX
    assert "setAttribute('aria-hidden', 'false')" in APP_JS
    assert "setAttribute('aria-hidden', 'true')" in APP_JS


def test_mobile_navigation_exposes_the_current_core_workflow() -> None:
    for tab in ("overview", "research", "analyse", "historie", "rebalancing", "evaluation"):
        assert f'class="bottom-nav-item" data-tab="{tab}"' in INDEX or (
            f'class="bottom-nav-item active" data-tab="{tab}"' in INDEX
        )
    assert '.bottom-nav-inner::-webkit-scrollbar' in STYLES
    assert 'flex: 1 0 58px' in STYLES


def test_experimental_advisor_navigation_follows_the_feature_flag() -> None:
    assert INDEX.count('data-tab="advisor" data-feature="trade_advisor"') == 2
    assert "fundMode && flags.trade_advisor" in APP_JS


def test_fund_research_mode_does_not_load_legacy_live_analytics() -> None:
    assert INDEX.count('data-personal-only="legacy_live_analytics"') == 3
    assert "if (!isFundResearchMode()) {\n                renderMarketIndices();" in APP_JS


def test_disabled_parqet_entry_is_controlled_by_server_feature_flag() -> None:
    assert 'id="btnUpdateParqet" data-feature="parqet"' in INDEX
    assert "document.querySelectorAll('[data-feature]')" in APP_JS


def test_unconfigured_fmp_usage_is_not_polled_or_shown_in_research_mode() -> None:
    assert "syncFmpUsagePolling();" in APP_JS
    assert "isFundResearchMode() || !appSettingsCache?.fmp_configured" in APP_JS
    assert "document.getElementById('fmpUsage')?.remove();" in APP_JS


def test_read_only_mode_is_visible_and_write_actions_are_not_offered() -> None:
    assert "Boolean(data.read_only_demo)" in APP_JS
    assert "`${modeLabel} · ${t('readOnly')}`" in APP_JS
    assert "document.querySelectorAll('[data-write-action]')" in APP_JS
    assert INDEX.count("data-write-action") >= 6
    assert 'data-write-action onclick="loadStructuredAiAnalysis(true)"' in INDEX


def test_workspace_money_and_status_helpers_execute_in_node() -> None:
    script = """
const workspace = require('./static/research-workspace.js');
console.log(JSON.stringify({
  usd: workspace.formatMoney(100, 'USD', 'en-US'),
  cny: workspace.formatMoney(100, 'CNY', 'zh-CN'),
  ok: workspace.statusTone('complete'),
  warn: workspace.statusTone('stale'),
  error: workspace.statusTone('rebuild_required')
}));
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["usd"] == "$100.00"
    assert payload["cny"].endswith("100.00")
    assert payload["cny"] != payload["usd"]
    assert payload["ok"] == "ok"
    assert payload["warn"] == "warn"
    assert payload["error"] == "error"
