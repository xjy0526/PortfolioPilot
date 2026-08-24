/** PostgreSQL-backed research data and lineage workspace. */
(function initResearchWorkspace(global) {
    'use strict';

    const state = {
        portfolios: [],
        selectedPortfolioId: '',
        loaded: false,
        loading: false,
        inputHash: '',
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
            await loadSelectedPortfolio();
            state.loaded = true;
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
            await loadSelectedPortfolio();
            state.loaded = true;
        } finally {
            state.loading = false;
        }
    }

    function syncDashboardPortfolio() {
        if (typeof global.setActivePortfolioId === 'function') {
            global.setActivePortfolioId(state.selectedPortfolioId);
        }
    }

    async function loadSelectedPortfolio() {
        const id = encodeURIComponent(state.selectedPortfolioId);
        const [valuation, positions, transactions, risk] = await Promise.all([
            request(`/api/portfolios/${id}/valuation`),
            request(`/api/portfolios/${id}/positions`),
            request(`/api/portfolios/${id}/transactions`),
            request(`/api/portfolio/risk-summary?portfolio_id=${id}`),
        ]);
        const portfolio = state.portfolios.find(item => item.id === state.selectedPortfolioId);
        renderWorkspace({ portfolio, valuation, positions, transactions, risk });
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
        if (['complete', 'healthy', 'available', 'valid', 'ok'].includes(normalized)) return 'ok';
        if (['partial', 'stale', 'warning', 'insufficient_data'].includes(normalized)) return 'warn';
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
            return `<section class="core-band"><div class="core-band-header"><div><p class="core-band-kicker">Risk Analytics</p><h3>${text('风险指标', 'Risk analytics')}</h3></div></div><div class="core-inline-alert warn"><i data-lucide="circle-help"></i><span>${escapeHtml(errorMessage(result, text('风险指标暂不可用。', 'Risk metrics are unavailable.')))}</span></div></section>`;
        }
        const metrics = risk.portfolio_metrics || {};
        const statuses = risk.metric_status || {};
        const quality = risk.data_quality || {};
        return `
            <section class="core-band" aria-labelledby="coreRiskTitle">
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
            </section>`;
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
        if (!state.inputHash) return;
        try {
            await navigator.clipboard.writeText(state.inputHash);
            if (typeof showToast === 'function') showToast(text('输入哈希已复制', 'Input hash copied'), 'success');
        } catch (error) {
            if (typeof showToast === 'function') showToast(text('无法复制输入哈希', 'Could not copy input hash'), 'error');
        }
    }

    const api = {
        load,
        selectPortfolio,
        copyInputHash,
        formatMoney,
        formatPercent,
        statusTone,
    };
    global.PortfolioPilotResearch = api;
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
})(typeof window !== 'undefined' ? window : globalThis);
