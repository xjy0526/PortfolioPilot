/** PostgreSQL-backed research data and lineage workspace. */
(function initResearchWorkspace(global) {
    'use strict';

    const state = {
        portfolios: [],
        selectedPortfolioId: '',
        loaded: false,
        loading: false,
        inputHash: '',
        requestGeneration: 0,
    };

    function zh() {
        return typeof isZh === 'function' ? isZh() : true;
    }

    function text(zhText, enText) {
        return zh() ? zhText : enText;
    }

    function escapeHtml(value) {
        return String(value ?? '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function asNumber(value) {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : null;
    }

    function formatMoney(value, currency = 'USD', locale = zh() ? 'zh-CN' : 'en-US') {
        const numeric = asNumber(value);
        if (numeric === null) return '—';
        const normalizedCurrency = /^[A-Z]{3}$/.test(String(currency || '').toUpperCase())
            ? String(currency).toUpperCase()
            : 'USD';
        return new Intl.NumberFormat(locale, {
            style: 'currency',
            currency: normalizedCurrency,
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        }).format(numeric);
    }

    function formatPercent(value, digits = 1) {
        const numeric = asNumber(value);
        if (numeric === null) return '—';
        return `${(numeric * 100).toFixed(digits)}%`;
    }

    function formatNumber(value, digits = 4) {
        const numeric = asNumber(value);
        if (numeric === null) return '—';
        return new Intl.NumberFormat(zh() ? 'zh-CN' : 'en-US', {
            minimumFractionDigits: 0,
            maximumFractionDigits: digits,
        }).format(numeric);
    }

    function formatDateTime(value) {
        if (!value) return '—';
        const parsed = new Date(value);
        if (Number.isNaN(parsed.getTime())) return escapeHtml(value);
        return parsed.toLocaleString(zh() ? 'zh-CN' : 'en-US', {
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
            hour12: false,
        });
    }

    async function request(path) {
        try {
            const response = await fetch(path, {
                headers: { Accept: 'application/json' },
                credentials: 'same-origin',
            });
            const payload = await response.json().catch(() => ({}));
            return { ok: response.ok, status: response.status, data: payload };
        } catch (error) {
            return { ok: false, status: 0, data: {}, error: String(error) };
        }
    }

    function renderLoading() {
        const container = document.getElementById('coreResearchWorkspace');
        if (!container) return;
        container.innerHTML = `<div class="empty-state core-loading-state"><span class="core-spinner" aria-hidden="true"></span>${text('正在读取 PostgreSQL 研究数据...', 'Loading PostgreSQL research data...')}</div>`;
    }

    function renderFatal(message) {
        const container = document.getElementById('coreResearchWorkspace');
        if (!container) return;
        container.innerHTML = `
            <div class="core-empty-panel" role="alert">
                <i data-lucide="database-x"></i>
                <div>
                    <strong>${text('研究数据暂不可用', 'Research data is unavailable')}</strong>
                    <p>${escapeHtml(message)}</p>
                </div>
            </div>`;
        if (global.lucide) global.lucide.createIcons();
    }

    function populatePortfolioSelect() {
        const select = document.getElementById('corePortfolioSelect');
        if (!select) return;
        select.innerHTML = state.portfolios
            .map(item => `<option value="${escapeHtml(item.id)}"${item.id === state.selectedPortfolioId ? ' selected' : ''}>${escapeHtml(item.name)} · ${escapeHtml(item.base_currency)}</option>`)
            .join('');
    }

    async function load(force = false) {
        if (state.loading || (state.loaded && !force)) return;
        state.loading = true;
        renderLoading();
        try {
            const portfoliosResult = await request('/api/portfolios');
            if (!portfoliosResult.ok || !Array.isArray(portfoliosResult.data)) {
                renderFatal(errorMessage(portfoliosResult, text('无法读取可访问组合。', 'Unable to load accessible portfolios.')));
                return;
            }
            state.portfolios = portfoliosResult.data;
            if (!state.portfolios.length) {
                document.getElementById('corePortfolioSelect').innerHTML = `<option value="">${text('暂无组合', 'No portfolios')}</option>`;
                renderFatal(text('当前身份没有可访问组合。请先创建组合或检查服务端权限。', 'The current principal has no accessible portfolio. Create one or check server-side permissions.'));
                return;
            }
            const activePortfolioId = typeof global.getActivePortfolioId === 'function'
                ? global.getActivePortfolioId()
                : '';
            if (!state.portfolios.some(item => item.id === state.selectedPortfolioId)) {
                state.selectedPortfolioId = state.portfolios.some(item => item.id === activePortfolioId)
                    ? activePortfolioId
                    : state.portfolios[0].id;
            }
            syncDashboardPortfolio();
            populatePortfolioSelect();
            const rendered = await loadSelectedPortfolio();
            if (rendered) state.loaded = true;
        } finally {
            state.loading = false;
        }
    }

    async function selectPortfolio(portfolioId) {
        if (!state.portfolios.some(item => item.id === portfolioId)) return;
        state.selectedPortfolioId = portfolioId;
        syncDashboardPortfolio();
        populatePortfolioSelect();
        state.loading = true;
        renderLoading();
        try {
            const rendered = await loadSelectedPortfolio();
            if (rendered) state.loaded = true;
        } finally {
            if (state.selectedPortfolioId === portfolioId) state.loading = false;
        }
    }

    function syncDashboardPortfolio() {
        if (typeof global.setActivePortfolioId === 'function') {
            global.setActivePortfolioId(state.selectedPortfolioId);
        }
    }

    async function loadSelectedPortfolio() {
        const selectedPortfolioId = state.selectedPortfolioId;
        const requestGeneration = ++state.requestGeneration;
        const id = encodeURIComponent(selectedPortfolioId);
        const [valuation, positions, transactions, risk, governance] = await Promise.all([
            request(`/api/portfolios/${id}/valuation`),
            request(`/api/portfolios/${id}/positions`),
            request(`/api/portfolios/${id}/transactions`),
            request(`/api/portfolio/risk-summary?portfolio_id=${id}`),
            request(`/api/portfolios/${id}/research-run`),
        ]);
        if (
            requestGeneration !== state.requestGeneration
            || selectedPortfolioId !== state.selectedPortfolioId
        ) {
            return false;
        }
        const portfolio = state.portfolios.find(item => item.id === selectedPortfolioId);
        renderWorkspace({ portfolio, valuation, positions, transactions, risk, governance });
        return true;
    }

    function errorMessage(result, fallback) {
        return result?.data?.detail?.reason
            || result?.data?.detail
            || result?.data?.reason
            || result?.data?.error
            || result?.error
            || fallback;
    }

    function statusTone(status) {
        const normalized = String(status || '').toLowerCase();
        if (['complete', 'completed', 'healthy', 'available', 'valid', 'ok', 'passed', 'published', 'approved', 'allowed', 'success'].includes(normalized)) return 'ok';
        if (['partial', 'stale', 'warning', 'insufficient_data', 'evidence_insufficient', 'unavailable', 'pending', 'pending_review', 'changes_requested'].includes(normalized)) return 'warn';
        return 'error';
    }

    function riskLevelLabel(level) {
        const normalized = String(level || '').toLowerCase();
        const labels = {
            low: text('低', 'Low'),
            medium: text('中', 'Medium'),
            high: text('高', 'High'),
        };
        return labels[normalized] || level || '—';
    }

    function metricStatusLabel(status) {
        const normalized = String(status || '').toLowerCase();
        const labels = {
            valid: text('有效', 'valid'),
            stale: text('行情过期', 'stale data'),
            insufficient_data: text('样本不足', 'insufficient data'),
            unavailable: text('不可用', 'unavailable'),
        };
        return labels[normalized] || (normalized ? normalized.replaceAll('_', ' ') : '—');
    }

    function concentrationFlagLabel(flag) {
        const value = String(flag || '');
        const singleAsset = value.match(/^single_asset:([^:]+):(.+)$/);
        if (singleAsset) {
            return text(
                `单资产集中：${singleAsset[1]} ${singleAsset[2]}`,
                `Single-asset concentration: ${singleAsset[1]} ${singleAsset[2]}`,
            );
        }
        const sector = value.match(/^sector:([^:]+):(.+)$/);
        if (sector) {
            return text(
                `行业集中：${sector[1]} ${sector[2]}`,
                `Sector concentration: ${sector[1]} ${sector[2]}`,
            );
        }
        const predictionMarket = value.match(/^prediction_market:(.+)$/);
        if (predictionMarket) {
            return text(
                `预测市场暴露：${predictionMarket[1]}`,
                `Prediction-market exposure: ${predictionMarket[1]}`,
            );
        }
        return value.replaceAll('_', ' ');
    }

    function metric(label, value, detail = '') {
        return `<div class="core-metric"><dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd>${detail ? `<small>${escapeHtml(detail)}</small>` : ''}</div>`;
    }

    function renderWorkspace(results) {
        const container = document.getElementById('coreResearchWorkspace');
        if (!container || !results.portfolio) return;
        const valuation = results.valuation.ok ? results.valuation.data : null;
        const positions = results.positions.ok ? results.positions.data : null;
        const transactions = results.transactions.ok && Array.isArray(results.transactions.data)
            ? results.transactions.data
            : [];
        const risk = results.risk.ok ? results.risk.data : null;
        const currency = valuation?.base_currency || results.portfolio.base_currency || 'USD';
        state.inputHash = valuation?.input_hash || '';

        const valuationError = valuation
            ? ''
            : errorMessage(results.valuation, text('尚未生成估值快照。', 'No valuation snapshot has been generated.'));
        const rebuildRequired = results.valuation.status === 409
            || results.valuation.data?.error === 'portfolio_rebuild_required'
            || results.valuation.data?.detail?.error === 'portfolio_rebuild_required';
        const valuationStatus = valuation?.valuation_status || (rebuildRequired ? 'rebuild_required' : 'unavailable');
        const valuationDetail = rebuildRequired
            ? text('血缘不匹配，系统已 fail closed。', 'Lineage mismatch; the system failed closed.')
            : valuationError;

        container.innerHTML = `
            <section class="core-band core-snapshot-band" aria-labelledby="coreSnapshotTitle">
                <div class="core-band-header">
                    <div>
                        <p class="core-band-kicker">Portfolio Snapshot</p>
                        <h3 id="coreSnapshotTitle">${escapeHtml(results.portfolio.name)}</h3>
                    </div>
                    <span class="core-status-badge ${statusTone(valuationStatus)}"><span aria-hidden="true"></span>${escapeHtml(valuationStatus)}</span>
                </div>
                <dl class="core-metric-grid">
                    ${metric(text('组合市值', 'Market value'), formatMoney(valuation?.total_market_value, currency), currency)}
                    ${metric(text('估值时点', 'Valuation as of'), formatDateTime(valuation?.as_of))}
                    ${metric(text('数据截止', 'Data as of'), formatDateTime(valuation?.data_as_of))}
                    ${metric(text('行情覆盖率', 'Price coverage'), valuation ? formatPercent(valuation.coverage_ratio) : '—', valuation ? `${Number(valuation.priced_asset_count || 0)} / ${Number(valuation.priced_asset_count || 0) + Number(valuation.unpriced_asset_count || 0)}` : '')}
                    ${metric(text('历史完整性', 'History completeness'), valuation?.history_completeness || positions?.history_completeness || '—')}
                    ${metric(text('事实来源', 'Source of truth'), valuation?.source || 'postgresql_ledger')}
                </dl>
                <div class="core-lineage-row">
                    <span>${text('输入哈希', 'Input hash')}</span>
                    <code title="${escapeHtml(state.inputHash)}">${state.inputHash ? escapeHtml(state.inputHash) : '—'}</code>
                    <button type="button" class="core-copy-button" onclick="PortfolioPilotResearch.copyInputHash()" ${state.inputHash ? '' : 'disabled'} title="${text('复制输入哈希', 'Copy input hash')}">
                        <i data-lucide="copy"></i><span class="sr-only">${text('复制输入哈希', 'Copy input hash')}</span>
                    </button>
                </div>
                ${valuationDetail ? `<div class="core-inline-alert ${rebuildRequired ? 'error' : 'warn'}"><i data-lucide="triangle-alert"></i><span>${escapeHtml(valuationDetail)}</span></div>` : ''}
            </section>

            ${renderQualityBand(valuation, positions)}
            ${renderRiskBand(risk, results.risk)}
            ${renderGovernanceWorkspace(results.governance)}
            ${renderPositionsBand(valuation?.positions || [], positions, currency)}
            ${renderActivitiesBand(transactions, results.transactions)}
        `;
        if (global.lucide) global.lucide.createIcons();
    }

    function renderQualityBand(valuation, positions) {
        const warnings = [...(valuation?.warnings || []), ...(positions?.warnings || [])];
        const missing = valuation?.unpriced_assets || [];
        const qualityStatus = valuation?.valuation_status || 'unavailable';
        return `
            <section class="core-band" aria-labelledby="coreQualityTitle">
                <div class="core-band-header">
                    <div><p class="core-band-kicker">Data Quality</p><h3 id="coreQualityTitle">${text('数据质量与可用性', 'Data quality and availability')}</h3></div>
                    <span class="core-status-badge ${statusTone(qualityStatus)}"><span aria-hidden="true"></span>${escapeHtml(qualityStatus)}</span>
                </div>
                <div class="core-quality-grid">
                    <div><span>${text('最早数据时点', 'Earliest data')}</span><strong>${formatDateTime(valuation?.data_as_of_earliest)}</strong></div>
                    <div><span>${text('最新数据时点', 'Latest data')}</span><strong>${formatDateTime(valuation?.data_as_of_latest)}</strong></div>
                    <div><span>${text('最大陈旧天数', 'Max staleness')}</span><strong>${valuation?.max_staleness_days ?? '—'}</strong></div>
                    <div><span>${text('未定价资产', 'Unpriced assets')}</span><strong>${missing.length}</strong></div>
                </div>
                ${missing.length ? `<div class="core-chip-list">${missing.map(item => `<span class="core-chip warn">${escapeHtml(typeof item === 'string' ? item : JSON.stringify(item))}</span>`).join('')}</div>` : ''}
                ${warnings.length ? `<ul class="core-warning-list">${warnings.map(item => `<li>${escapeHtml(typeof item === 'string' ? item : JSON.stringify(item))}</li>`).join('')}</ul>` : `<p class="core-muted">${text('当前快照没有额外数据质量警告。', 'The current snapshot has no additional data-quality warnings.')}</p>`}
            </section>`;
    }

    function renderRiskBand(risk, result) {
        if (!risk) {
            return `<section class="core-band showcase-risk-band"><div class="core-band-header"><div><p class="core-band-kicker">Risk Analytics</p><h3>${text('风险指标', 'Risk analytics')}</h3></div></div><div class="core-inline-alert warn"><i data-lucide="circle-help"></i><span>${escapeHtml(errorMessage(result, text('风险指标暂不可用。', 'Risk metrics are unavailable.')))}</span></div></section>`;
        }
        const metrics = risk.portfolio_metrics || {};
        const statuses = risk.metric_status || {};
        const quality = risk.data_quality || {};
        return `
            <section class="core-band showcase-risk-band" aria-labelledby="coreRiskTitle">
                <div class="core-band-header">
                    <div><p class="core-band-kicker">Risk Analytics</p><h3 id="coreRiskTitle">${text('确定性风险摘要', 'Deterministic risk summary')}</h3></div>
                    <span class="core-status-badge ${statusTone(quality.status)}"><span aria-hidden="true"></span>${escapeHtml(quality.status || 'unavailable')}</span>
                </div>
                <dl class="core-metric-grid core-risk-grid">
                    ${metric(text('风险分', 'Risk score'), `${asNumber(risk.risk_score)?.toFixed(1) ?? '—'} / 10`, riskLevelLabel(risk.risk_level))}
                    ${metric(text('年化波动', 'Annual volatility'), metrics.annual_volatility == null ? '—' : formatPercent(metrics.annual_volatility), metricStatusLabel(statuses.annual_volatility))}
                    ${metric(text('最大回撤', 'Max drawdown'), metrics.max_drawdown == null ? '—' : formatPercent(metrics.max_drawdown), metricStatusLabel(statuses.max_drawdown))}
                    ${metric('Sharpe', metrics.sharpe_ratio == null ? '—' : Number(metrics.sharpe_ratio).toFixed(2), metricStatusLabel(statuses.sharpe_ratio))}
                </dl>
                <div class="core-chip-list">${(risk.concentration_flags || []).map(item => `<span class="core-chip warn">${escapeHtml(concentrationFlagLabel(item))}</span>`).join('') || `<span class="core-chip">${text('无集中度规则告警', 'No concentration rule alerts')}</span>`}</div>
                <div class="showcase-risk-exposures">
                    ${renderExposureList(text('行业暴露', 'Sector exposure'), risk.sector_concentration)}
                    ${renderExposureList(text('资产类型暴露', 'Asset-type exposure'), risk.asset_type_exposure)}
                </div>
            </section>`;
    }

    function renderExposureList(title, exposure) {
        const rows = Object.entries(exposure || {}).sort((left, right) => Number(right[1]?.weight || 0) - Number(left[1]?.weight || 0));
        return `<div class="showcase-exposure-panel"><h4>${escapeHtml(title)}</h4>${rows.map(([name, item]) => {
            const weight = Math.max(0, Math.min(1, Number(item?.weight || 0)));
            return `<div class="showcase-exposure-row"><div><span>${escapeHtml(name)}</span><strong>${formatPercent(weight)}</strong></div><div class="showcase-exposure-track" role="img" aria-label="${escapeHtml(name)} ${formatPercent(weight)}"><span style="width:${(weight * 100).toFixed(2)}%"></span></div></div>`;
        }).join('') || `<p class="core-muted">${text('暂无暴露数据。', 'Exposure data is unavailable.')}</p>`}</div>`;
    }

    function renderGovernanceWorkspace(result) {
        if (!result?.ok) {
            const missing = result?.status === 404;
            return `
                <section class="core-band showcase-governance-empty" aria-labelledby="showcaseGovernanceTitle">
                    <div class="core-band-header">
                        <div><p class="core-band-kicker">Research Workflow</p><h3 id="showcaseGovernanceTitle">${text('研究运行详情', 'Research run details')}</h3></div>
                        <span class="core-status-badge ${missing ? 'warn' : 'error'}"><span aria-hidden="true"></span>${missing ? 'empty' : 'error'}</span>
                    </div>
                    <div class="core-inline-alert ${missing ? '' : 'error'}" role="status">
                        <i data-lucide="${missing ? 'file-question' : 'circle-x'}"></i>
                        <div><strong>${missing ? text('尚无关联研究运行', 'No linked research run') : text('研究运行不可用', 'Research run unavailable')}</strong><p>${escapeHtml(errorMessage(result, text('请先创建并推进研究 Workflow。', 'Create and process a research workflow first.')))}</p></div>
                    </div>
                </section>`;
        }
        const data = result.data || {};
        return `
            ${renderTruthBoundary(data)}
            ${renderEvidenceBand(data.evidence)}
            ${renderPromptTraceBand(data)}
            ${renderValidationBand(data.validation, data.run)}
            ${renderReviewBand(data.review_timeline, data.truth_labels)}
            ${renderPublishedReportBand(data.published_report, data.lineage)}
        `;
    }

    function renderTruthBoundary(data) {
        const labels = data.truth_labels || {};
        const run = data.run || {};
        return `
            <section class="core-band showcase-run-band" aria-labelledby="showcaseRunTitle">
                <div class="core-band-header">
                    <div>
                        <p class="core-band-kicker">Research Run</p>
                        <h3 id="showcaseRunTitle">${text('证据驱动研究运行', 'Evidence-driven research run')}</h3>
                        <p>${text('所有状态均来自 PostgreSQL 中的 Workflow、Trace 与审核记录。', 'Every state is read from persisted Workflow, Trace, and review records.')}</p>
                    </div>
                    <span class="core-status-badge ${statusTone(run.status)}"><span aria-hidden="true"></span>${escapeHtml(run.status || 'unavailable')}</span>
                </div>
                <div class="showcase-disclosure-strip" role="note" aria-label="Research output disclosure">
                    ${labels.synthetic_data_used === true
                        ? '<strong class="showcase-disclosure synthetic">Synthetic Demo</strong>'
                        : `<strong class="showcase-disclosure ${labels.synthetic_data_used === false ? '' : 'mock'}">synthetic_data_used=${recordedBoolean(labels.synthetic_data_used)}</strong>`}
                    ${labels.mock_response_used === true
                        ? '<strong class="showcase-disclosure mock">Mock Model</strong>'
                        : labels.real_model_used === true
                            ? '<strong class="showcase-disclosure synthetic">Real Model</strong>'
                            : `<strong class="showcase-disclosure mock">model_mode=${recordedBoolean(labels.real_model_used)}</strong>`}
                    <strong class="showcase-disclosure advice">Not Investment Advice</strong>
                </div>
                <dl class="core-metric-grid">
                    ${metric(text('运行 ID', 'Run ID'), shortId(run.run_id), run.run_id || '—')}
                    ${metric(text('业务场景', 'Business scene'), run.business_scene || '—')}
                    ${metric(text('代码版本', 'Code version'), run.code_version || '—')}
                    ${metric('mock_response_used', recordedBoolean(labels.mock_response_used))}
                    ${metric('real_model_used', recordedBoolean(labels.real_model_used))}
                    ${metric('production_data_used', recordedBoolean(labels.production_data_used))}
                </dl>
                ${renderCopyLineage(text('Workflow lineage', 'Workflow lineage'), run.run_id)}
                ${run.error_type ? `<div class="core-inline-alert error" role="alert"><i data-lucide="circle-x"></i><span>${text('运行失败：', 'Run failed: ')}${escapeHtml(run.error_type)}</span></div>` : ''}
            </section>`;
    }

    function renderEvidenceBand(evidence = {}) {
        const items = Array.isArray(evidence.items) ? evidence.items : [];
        const evidenceStatus = evidence.status || 'evidence_insufficient';
        return `
            <section class="core-band showcase-evidence-band" aria-labelledby="showcaseEvidenceTitle">
                <div class="core-band-header">
                    <div>
                        <p class="core-band-kicker">Evidence & Citation</p>
                        <h3 id="showcaseEvidenceTitle">${text('引用证据与权限状态', 'Cited evidence and permission status')}</h3>
                        <p>${text('只显示当前服务端 Principal 在研究时点可见的已发布 Chunk。', 'Only published chunks visible to the server-side principal at the research cutoff are shown.')}</p>
                    </div>
                    <span class="core-status-badge ${statusTone(evidenceStatus)}"><span aria-hidden="true"></span>${escapeHtml(evidenceStatus)}</span>
                </div>
                <div class="core-quality-grid showcase-evidence-counts">
                    <div><span>${text('Trace 引用', 'Trace references')}</span><strong>${Number(evidence.requested_count || 0)}</strong></div>
                    <div><span>${text('可见有效', 'Visible and valid')}</span><strong>${Number(evidence.visible_count || 0)}</strong></div>
                    <div><span>${text('无权或失效', 'Withheld or invalid')}</span><strong>${Number(evidence.withheld_or_invalid_count || 0)}</strong></div>
                    <div><span>${text('证据状态', 'Evidence status')}</span><strong>${escapeHtml(evidenceStatus)}</strong></div>
                </div>
                ${evidenceStatus === 'evidence_insufficient' ? `<div class="core-inline-alert warn" role="status"><i data-lucide="search-x"></i><div><strong>evidence_insufficient</strong><p>${text('没有足够的可见证据支持展示结论；系统不会补造引用。', 'There is not enough visible evidence to support a claim; no citation is fabricated.')}</p></div></div>` : ''}
                <div class="showcase-evidence-list">
                    ${items.map(item => `
                        <article class="showcase-evidence-item">
                            <header><div><strong>${escapeHtml(item.title || item.source_filename || 'Untitled document')}</strong><small>v${escapeHtml(item.version ?? '—')} · ${escapeHtml(item.section || text('未标注章节', 'Unlabelled section'))}${item.page_number ? ` · p.${escapeHtml(item.page_number)}` : ''}</small></div><span class="core-status-badge ${statusTone(item.validity_status)}"><span aria-hidden="true"></span>${escapeHtml(item.validity_status || 'unavailable')}</span></header>
                            <blockquote>${escapeHtml(item.quote || '')}</blockquote>
                            <dl class="showcase-evidence-meta">
                                <div><dt>${text('检索分数', 'Retrieval score')}</dt><dd>${item.retrieval_score == null ? `— · ${escapeHtml(item.score_status || 'unavailable')}` : formatNumber(item.retrieval_score)}</dd></div>
                                <div><dt>${text('权限', 'Permission')}</dt><dd>${escapeHtml(item.permission_status || 'unavailable')}</dd></div>
                                <div><dt>Chunk</dt><dd><code title="${escapeHtml(item.chunk_id || '')}">${escapeHtml(shortId(item.chunk_id))}</code></dd></div>
                            </dl>
                        </article>`).join('') || `<div class="core-empty-panel"><i data-lucide="search-x"></i><div><strong>${text('没有可展示证据', 'No evidence to display')}</strong><p>${text('请检查检索结果、文档时效或服务端权限。', 'Check retrieval results, document validity, or server-side permissions.')}</p></div></div>`}
                </div>
            </section>`;
    }

    function renderPromptTraceBand(data) {
        const prompt = data.prompt;
        const trace = data.trace;
        const labels = data.truth_labels || {};
        return `
            <section class="core-band showcase-trace-review" aria-labelledby="showcaseTraceTitle">
                <div class="core-band-header">
                    <div><p class="core-band-kicker">Prompt & LLM Trace</p><h3 id="showcaseTraceTitle">${text('Prompt 版本与模型调用血缘', 'Prompt version and model-call lineage')}</h3></div>
                    <span class="core-status-badge ${statusTone(trace?.status || 'unavailable')}"><span aria-hidden="true"></span>${escapeHtml(trace?.status || 'unavailable')}</span>
                </div>
                <div class="showcase-two-column">
                    <article class="showcase-detail-panel">
                        <h4>${text('已发布 Prompt', 'Published prompt')}</h4>
                        ${prompt ? `<dl class="showcase-detail-list">
                            <div><dt>Prompt key</dt><dd>${escapeHtml(prompt.prompt_key)}</dd></div>
                            <div><dt>${text('版本', 'Version')}</dt><dd>v${escapeHtml(prompt.version)} · ${escapeHtml(prompt.status)}</dd></div>
                            <div><dt>${text('模型配置', 'Model config')}</dt><dd>${escapeHtml(prompt.model)} · temp ${formatNumber(prompt.temperature, 2)}</dd></div>
                            <div><dt>${text('发布时间', 'Published')}</dt><dd>${formatDateTime(prompt.published_at)}</dd></div>
                        </dl>${renderCopyLineage('Prompt version ID', prompt.version_id)}` : renderUnavailable(text('Prompt 版本未记录', 'Prompt version is not recorded'), 'prompt_version_missing')}
                    </article>
                    <article class="showcase-detail-panel">
                        <h4>${text('LLM Trace', 'LLM trace')}</h4>
                        ${trace ? `<dl class="showcase-detail-list">
                            <div><dt>Provider / Model</dt><dd>${escapeHtml(trace.provider)} / ${escapeHtml(trace.model)}</dd></div>
                            <div><dt>${text('模型模式', 'Model mode')}</dt><dd><strong>${labels.mock_response_used === true ? 'Mock Model' : labels.real_model_used === true ? 'Real Model' : 'Not recorded'}</strong></dd></div>
                            <div><dt>Tokens</dt><dd>${trace.input_tokens ?? '—'} in / ${trace.output_tokens ?? '—'} out · ${escapeHtml(trace.usage_source || 'unavailable')}</dd></div>
                            <div><dt>${text('成本', 'Cost')}</dt><dd>${trace.cost_amount == null ? '—' : `${formatNumber(trace.cost_amount, 6)} ${escapeHtml(trace.cost_currency)}`} · ${escapeHtml(trace.cost_source || 'unavailable')}</dd></div>
                            <div><dt>fallback</dt><dd class="${trace.fallback_used ? 'showcase-danger-text' : ''}">${recordedBoolean(trace.fallback_used)}</dd></div>
                            <div><dt>mock_response_used</dt><dd>${recordedBoolean(labels.mock_response_used)}</dd></div>
                        </dl>${renderCopyLineage('Trace ID', trace.trace_id)}${trace.error_message ? `<div class="core-inline-alert error"><i data-lucide="circle-x"></i><span>${escapeHtml(trace.error_message)}</span></div>` : ''}` : renderUnavailable(text('模型 Trace 未记录', 'Model trace is not recorded'), 'llm_trace_missing')}
                    </article>
                </div>
            </section>`;
    }

    function renderValidationBand(validation, run) {
        const rows = Array.isArray(validation) ? validation : [];
        return `
            <section class="core-band showcase-validation-band" aria-labelledby="showcaseValidationTitle">
                <div class="core-band-header">
                    <div><p class="core-band-kicker">Deterministic Validation</p><h3 id="showcaseValidationTitle">${text('规则校验', 'Rule validation')}</h3><p>${text('未执行或未记录的检查明确显示 unavailable。', 'Checks that were not run or recorded remain explicitly unavailable.')}</p></div>
                    <span class="core-count">${rows.length}</span>
                </div>
                <div class="showcase-validation-list">
                    ${rows.map(item => `<div class="showcase-validation-row"><div><strong>${escapeHtml(item.key)}</strong><small>${escapeHtml(item.reason || '—')}</small></div><span class="core-status-badge ${statusTone(item.status)}"><span aria-hidden="true"></span>${escapeHtml(item.status)}</span></div>`).join('') || renderUnavailable(text('没有校验记录', 'No validation record'), 'validation_not_recorded')}
                </div>
                ${['FAILED', 'REJECTED'].includes(String(run?.status || '').toUpperCase()) ? `<div class="core-inline-alert error" role="alert"><i data-lucide="ban"></i><strong>${escapeHtml(run.status)}</strong></div>` : ''}
            </section>`;
    }

    function renderReviewBand(timeline, labels = {}) {
        const rows = Array.isArray(timeline) ? timeline : [];
        return `
            <section class="core-band showcase-review-band" aria-labelledby="showcaseReviewTitle">
                <div class="core-band-header">
                    <div><p class="core-band-kicker">Human Review Timeline</p><h3 id="showcaseReviewTitle">${text('审核时间线', 'Review timeline')}</h3><p>${text('真实工作流身份由服务端 Principal 写入；Demo 审核明确标记为 synthetic fixture。', 'Production reviewer identity comes from the server principal; demo review is explicitly a synthetic fixture.')}</p></div>
                    <span class="core-chip ${labels.human_label_used === true ? '' : 'warn'}">human_label_used=${recordedBoolean(labels.human_label_used)}</span>
                </div>
                <ol class="showcase-timeline">
                    ${rows.map(item => `<li><span class="showcase-timeline-marker ${statusTone(item.status)}" aria-hidden="true"></span><div><header><strong>${escapeHtml(item.status || 'PENDING')}</strong><time>${formatDateTime(item.completed_at || item.created_at)}</time></header><p>${escapeHtml(item.decision || text('等待审核决定', 'Awaiting review decision'))} · ${escapeHtml(item.reviewer_id || item.assigned_group || '—')}</p><small>${escapeHtml(item.feedback || text('没有反馈记录', 'No feedback recorded'))} · identity=${escapeHtml(item.identity_source || 'server_principal')}</small></div></li>`).join('') || `<li><span class="showcase-timeline-marker warn" aria-hidden="true"></span><div><strong>PENDING</strong><p>${text('尚未创建 Review Task。', 'No Review Task has been created.')}</p></div></li>`}
                </ol>
            </section>`;
    }

    function renderPublishedReportBand(report, lineage = {}) {
        const payload = report?.report || {};
        const observations = Array.isArray(payload.research_observations) ? payload.research_observations : [];
        return `
            <section class="core-band showcase-report-band" aria-labelledby="showcaseReportTitle">
                <div class="core-band-header">
                    <div><p class="core-band-kicker">Published Report</p><h3 id="showcaseReportTitle">${escapeHtml(payload.title || text('已发布研究报告', 'Published research report'))}</h3></div>
                    <span class="core-status-badge ${report ? 'ok' : 'warn'}"><span aria-hidden="true"></span>${report ? 'published' : 'unavailable'}</span>
                </div>
                ${report ? `<div class="showcase-report-body"><p class="showcase-report-summary">${escapeHtml(payload.summary || '—')}</p><ul>${observations.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul><p class="showcase-report-disclaimer">${escapeHtml(payload.disclaimer || 'Not Investment Advice')}</p></div>
                    <dl class="showcase-detail-list showcase-report-meta"><div><dt>Report ID</dt><dd>${escapeHtml(report.report_id)}</dd></div><div><dt>${text('来源 Workflow', 'Source workflow')}</dt><dd>${escapeHtml(report.workflow_run_id)}</dd></div><div><dt>${text('发布时点', 'Published at')}</dt><dd>${formatDateTime(report.published_at)}</dd></div><div><dt>${text('发布身份', 'Published by')}</dt><dd>${escapeHtml(report.published_by)}</dd></div></dl>
                    ${renderCopyLineage(text('完整 Provenance', 'Full provenance'), [lineage.workflow_run_id, lineage.prompt_version_id, lineage.llm_trace_id, lineage.report_id].filter(Boolean).join(' · '))}`
                    : renderUnavailable(text('研究报告尚未发布', 'Research report has not been published'), 'published_report_missing')}
            </section>`;
    }

    function renderUnavailable(title, reason) {
        return `<div class="core-inline-alert warn" role="status"><i data-lucide="circle-help"></i><div><strong>${escapeHtml(title)}</strong><p>${escapeHtml(reason)}</p></div></div>`;
    }

    function recordedBoolean(value) {
        return value === true ? 'true' : value === false ? 'false' : 'not_recorded';
    }

    function shortId(value) {
        const normalized = String(value || '');
        return normalized.length > 16 ? `${normalized.slice(0, 8)}…${normalized.slice(-6)}` : normalized || '—';
    }

    function renderCopyLineage(label, value) {
        const normalized = String(value || '');
        return `<div class="core-lineage-row"><span>${escapeHtml(label)}</span><code title="${escapeHtml(normalized)}">${escapeHtml(normalized || '—')}</code><button type="button" class="core-copy-button" data-copy-value="${escapeHtml(normalized)}" onclick="PortfolioPilotResearch.copyValue(this.dataset.copyValue)" ${normalized ? '' : 'disabled'} title="${text('复制血缘标识', 'Copy lineage identifier')}"><i data-lucide="copy"></i><span class="sr-only">${text('复制血缘标识', 'Copy lineage identifier')}</span></button></div>`;
    }

    function renderPositionsBand(valuationPositions, rebuilt, currency) {
        const valuationBySecurity = new Map(valuationPositions.map(item => [item.security_id, item]));
        const rows = Array.isArray(rebuilt?.positions) ? rebuilt.positions : [];
        return `
            <section class="core-band" aria-labelledby="corePositionsTitle">
                <div class="core-band-header">
                    <div><p class="core-band-kicker">Effective Positions</p><h3 id="corePositionsTitle">${text('账本重建持仓', 'Ledger-rebuilt positions')}</h3></div>
                    <span class="core-count">${rows.length}</span>
                </div>
                <div class="core-table-wrap"><table class="core-table"><thead><tr><th>${text('代码', 'Ticker')}</th><th>${text('数量', 'Quantity')}</th><th>${text('平均成本', 'Average cost')}</th><th>${text('快照市值', 'Snapshot value')}</th><th>${text('权重', 'Weight')}</th><th>${text('行情血缘', 'Price lineage')}</th></tr></thead><tbody>
                    ${rows.map(item => {
                        const snapshot = valuationBySecurity.get(item.security_id) || {};
                        return `<tr><td><strong>${escapeHtml(item.ticker || '—')}</strong><small>${escapeHtml(item.native_currency || '')}</small></td><td class="numeric">${formatNumber(item.quantity)}</td><td class="numeric">${formatMoney(item.average_cost_native, item.native_currency || currency)}</td><td class="numeric">${formatMoney(snapshot.market_value_base, currency)}</td><td class="numeric">${snapshot.weight == null ? '—' : formatPercent(snapshot.weight)}</td><td><code title="${escapeHtml(snapshot.price_bar_id || '')}">${snapshot.price_bar_id ? escapeHtml(snapshot.price_bar_id) : '—'}</code></td></tr>`;
                    }).join('') || `<tr><td colspan="6"><div class="empty-state">${text('当前时点没有证券持仓。', 'No security positions exist at this point in time.')}</div></td></tr>`}
                </tbody></table></div>
            </section>`;
    }

    function renderActivitiesBand(transactions, result) {
        if (!result.ok) {
            return `<section class="core-band"><div class="core-inline-alert error"><i data-lucide="circle-x"></i><span>${escapeHtml(errorMessage(result, text('无法读取有效活动。', 'Unable to load effective activity.')))}</span></div></section>`;
        }
        const rows = transactions.slice(-12).reverse();
        return `
            <section class="core-band" aria-labelledby="coreActivitiesTitle">
                <div class="core-band-header">
                    <div><p class="core-band-kicker">Effective Activity</p><h3 id="coreActivitiesTitle">${text('当前有效活动', 'Current effective activity')}</h3><p>${text('普通视图排除 superseded Legacy generations；完整历史仅在管理员 Audit API 中可见。', 'The ordinary view excludes superseded legacy generations; complete history remains in the administrator Audit API.')}</p></div>
                    <span class="core-count">${transactions.length}</span>
                </div>
                <div class="core-table-wrap"><table class="core-table"><thead><tr><th>${text('时间', 'Time')}</th><th>${text('类型', 'Type')}</th><th>${text('代码', 'Ticker')}</th><th>${text('数量', 'Quantity')}</th><th>${text('金额', 'Amount')}</th><th>${text('来源', 'Source')}</th></tr></thead><tbody>
                    ${rows.map(item => `<tr><td>${formatDateTime(item.occurred_at)}</td><td><span class="core-chip">${escapeHtml(item.transaction_type)}</span></td><td><strong>${escapeHtml(item.ticker || '—')}</strong></td><td class="numeric">${formatNumber(item.quantity)}</td><td class="numeric">${formatMoney(item.gross_amount, item.currency)}</td><td>${escapeHtml(item.source || '—')}</td></tr>`).join('') || `<tr><td colspan="6"><div class="empty-state">${text('暂无有效交易活动。', 'No effective transaction activity.')}</div></td></tr>`}
                </tbody></table></div>
            </section>`;
    }

    async function copyInputHash() {
        return copyValue(state.inputHash);
    }

    async function copyValue(value) {
        if (!value) return;
        try {
            await navigator.clipboard.writeText(value);
            if (typeof showToast === 'function') showToast(text('血缘标识已复制', 'Lineage identifier copied'), 'success');
        } catch (error) {
            if (typeof showToast === 'function') showToast(text('无法复制血缘标识', 'Could not copy lineage identifier'), 'error');
        }
    }

    const api = {
        load,
        selectPortfolio,
        copyInputHash,
        copyValue,
        formatMoney,
        formatPercent,
        recordedBoolean,
        statusTone,
    };
    global.PortfolioPilotResearch = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
