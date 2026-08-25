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
        "/api/portfolios/${id}/research-run",
    )
    for path in expected_paths:
        assert path in WORKSPACE_JS

    assert "/transactions/audit" not in WORKSPACE_JS
    assert "superseded legacy generations" in WORKSPACE_JS


def test_showcase_renders_the_complete_governed_research_chain() -> None:
    required_sections = (
        "Portfolio Snapshot",
        "Risk Analytics",
        "Sector exposure",
        "Asset-type exposure",
        "Evidence & Citation",
        "Prompt & LLM Trace",
        "Deterministic Validation",
        "Human Review Timeline",
        "Published Report",
    )
    for section in required_sections:
        assert section in WORKSPACE_JS

    for disclosure in (
        "Synthetic Demo",
        "Mock Model",
        "Not Investment Advice",
        "mock_response_used",
        "evidence_insufficient",
        "fallback",
        "item.reason",
    ):
        assert disclosure in WORKSPACE_JS


def test_showcase_has_accessible_empty_error_and_status_states() -> None:
    assert 'role="alert"' in WORKSPACE_JS
    assert 'role="status"' in WORKSPACE_JS
    assert 'aria-labelledby="showcaseEvidenceTitle"' in WORKSPACE_JS
    assert 'aria-labelledby="showcaseTraceTitle"' in WORKSPACE_JS
    assert 'aria-labelledby="showcaseReviewTitle"' in WORKSPACE_JS
    assert "research_run_not_found" not in WORKSPACE_JS
    assert "No linked research run" in WORKSPACE_JS
    assert "Research run unavailable" in WORKSPACE_JS


def test_showcase_does_not_hardcode_demo_database_identifiers() -> None:
    assert "DEMO_WORKFLOW_ID" not in WORKSPACE_JS
    assert "DEMO_TRACE_ID" not in WORKSPACE_JS
    assert "DEMO_REPORT_ID" not in WORKSPACE_JS
    assert "load_demo_manifest" not in WORKSPACE_JS


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


def test_workspace_discards_responses_for_a_stale_portfolio_selection() -> None:
    script = r"""
const elements = {
  coreResearchWorkspace: { innerHTML: '' },
  corePortfolioSelect: { innerHTML: '' },
};
global.document = { getElementById: id => elements[id] || null };
global.isZh = () => false;
global.setActivePortfolioId = () => {};

function response(data, ok = true, status = 200) {
  return { ok, status, json: async () => data };
}
function endpointResponse(path) {
  if (path.includes('/valuation')) {
    return response({
      base_currency: 'USD', total_market_value: 1, valuation_status: 'complete',
      coverage_ratio: 1, positions: [], warnings: [], unpriced_assets: [],
    });
  }
  if (path.includes('/positions')) return response({ positions: [], warnings: [] });
  if (path.includes('/transactions')) return response([]);
  if (path.includes('/risk-summary')) return response({});
  if (path.includes('/research-run')) return response({}, false, 404);
  throw new Error(`Unexpected endpoint: ${path}`);
}

global.fetch = async path => {
  if (path === '/api/portfolios') {
    return response([
      { id: 'A', name: 'Portfolio A', base_currency: 'USD' },
      { id: 'B', name: 'Portfolio B', base_currency: 'USD' },
    ]);
  }
  return endpointResponse(path);
};

const workspace = require('./static/research-workspace.js');
(async () => {
  await workspace.load(true);
  const pending = { A: [], B: [] };
  global.fetch = path => new Promise(resolve => {
    const parsed = new URL(path, 'http://localhost');
    const portfolioId = parsed.searchParams.get('portfolio_id') || parsed.pathname.split('/')[3];
    pending[portfolioId].push(() => resolve(endpointResponse(path)));
  });

  const requestA = workspace.selectPortfolio('A');
  const requestB = workspace.selectPortfolio('B');
  pending.B.forEach(resolve => resolve());
  await requestB;
  pending.A.forEach(resolve => resolve());
  await requestA;

  console.log(JSON.stringify({
    hasB: elements.coreResearchWorkspace.innerHTML.includes('Portfolio B'),
    hasA: elements.coreResearchWorkspace.innerHTML.includes('Portfolio A'),
  }));
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
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
    assert json.loads(result.stdout) == {"hasB": True, "hasA": False}


def test_startup_validates_stored_portfolio_access_before_scoped_loading() -> None:
    startup = APP_JS[APP_JS.index("document.addEventListener('DOMContentLoaded'") :]

    assert "async function validateActivePortfolioSelection()" in APP_JS
    assert "await validateActivePortfolioSelection();\n    await loadPortfolio();" in startup
    assert "fetch('/api/portfolios'" in APP_JS
    assert "portfolios.some(item => item.id === storedPortfolioId)" in APP_JS
    assert "localStorage.removeItem('portfoliopilot-active-portfolio')" in APP_JS


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
  error: workspace.statusTone('rebuild_required'),
  published: workspace.statusTone('PUBLISHED'),
  mock: workspace.recordedBoolean(true),
  real: workspace.recordedBoolean(false),
  unknown: workspace.recordedBoolean(null)
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
    assert payload["published"] == "ok"
    assert payload["mock"] == "true"
    assert payload["real"] == "false"
    assert payload["unknown"] == "not_recorded"
