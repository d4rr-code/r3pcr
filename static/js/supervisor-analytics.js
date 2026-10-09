(function () {
    'use strict';

    var configElement = document.getElementById('analytics-config');
    if (!configElement) return;

    var config;
    try {
        config = JSON.parse(configElement.textContent);
    } catch (error) {
        console.error('Unable to initialize analytics dashboard.', error);
        return;
    }

(function () {
    function total(values) {
        return values.reduce(function (sum, item) { return sum + Number(item || 0); }, 0);
    }

    function lastDayOfMonth(year, monthIndex) {
        return new Date(year, monthIndex + 1, 0).getDate();
    }

    function setMonthRange(params, monthValue) {
        if (!monthValue) return false;
        var parts = monthValue.split('-');
        var year = Number(parts[0]);
        var month = Number(parts[1]);
        if (!year || !month) return false;
        var lastDay = lastDayOfMonth(year, month - 1);
        params.set('date_from', monthValue + '-01');
        params.set('date_to', monthValue + '-' + String(lastDay).padStart(2, '0'));
        return true;
    }

    document.querySelectorAll('.js-month-filter').forEach(function (input) {
        input.addEventListener('change', function () {
            var params = new URLSearchParams(window.location.search);
            if (setMonthRange(params, input.value)) {
                window.location.search = params.toString();
            }
        });
    });

    function isoDate(date) {
        var y = date.getFullYear();
        var m = String(date.getMonth() + 1).padStart(2, '0');
        var d = String(date.getDate()).padStart(2, '0');
        return y + '-' + m + '-' + d;
    }

    // ── Generate Reports card ────────────────────────────────────────────────
    // The reporting-period presets resolve to date_from/date_to before submit,
    // so the backend keeps its existing date-range contract unchanged.
    (function reportsCard() {
        var form = document.getElementById('reports-filter-form');
        var periodSelect = document.getElementById('report-period');
        var customRange = document.getElementById('report-custom-range');
        var fromInput = document.getElementById('report-date-from');
        var toInput = document.getElementById('report-date-to');
        if (!form || !periodSelect || !customRange || !fromInput || !toInput) return;

        function rangeFor(period) {
            var today = new Date();
            var y = today.getFullYear();
            var m = today.getMonth();
            switch (period) {
                case 'today':
                    return [today, today];
                case 'this_week': {
                    var monday = new Date(today);
                    // getDay(): 0 = Sunday, so shift back to the preceding Monday.
                    monday.setDate(today.getDate() - ((today.getDay() + 6) % 7));
                    return [monday, today];
                }
                case 'this_month':
                    return [new Date(y, m, 1), today];
                case 'last_month':
                    return [new Date(y, m - 1, 1), new Date(y, m, 0)];
                case 'this_quarter':
                    return [new Date(y, Math.floor(m / 3) * 3, 1), today];
                case 'this_year':
                    return [new Date(y, 0, 1), today];
                default:
                    return null;
            }
        }

        function syncCustomVisibility() {
            customRange.hidden = periodSelect.value !== 'custom';
        }

        function applyPeriodToInputs() {
            var period = periodSelect.value;
            if (period === 'custom' || period === '') {
                if (period === '') {
                    fromInput.value = '';
                    toInput.value = '';
                }
                return;
            }
            var range = rangeFor(period);
            if (range) {
                fromInput.value = isoDate(range[0]);
                toInput.value = isoDate(range[1]);
            }
        }

        function currentFilterParams() {
            applyPeriodToInputs();
            var params = new URLSearchParams();
            // period is label-only for the report header; date_from/date_to
            // still drive the actual filtering.
            if (periodSelect.value) params.set('period', periodSelect.value);
            if (fromInput.value) params.set('date_from', fromInput.value);
            if (toInput.value) params.set('date_to', toInput.value);
            var declarant = document.getElementById('report-declarant');
            if (declarant && declarant.value) params.set('declarant', declarant.value);
            return params;
        }

        periodSelect.addEventListener('change', function () {
            syncCustomVisibility();
            applyPeriodToInputs();
        });
        form.addEventListener('submit', applyPeriodToInputs);

        function exportUrl(link) {
            if (!link.dataset.exportBase) return link.href;
            var params = currentFilterParams();
            params.set('format', link.dataset.exportFormat);
            return link.dataset.exportBase + '?' + params.toString();
        }

        function exportFilename(response, link) {
            var disposition = response.headers.get('Content-Disposition') || '';
            var encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i);
            var plain = disposition.match(/filename="?([^";]+)"?/i);
            if (encoded) return decodeURIComponent(encoded[1]);
            if (plain) return plain[1];
            var format = link.dataset.exportFormat || new URL(link.href).searchParams.get('format') || 'csv';
            return 'r3pcr-report.' + format;
        }

        function setExportState(link, label, className) {
            link.textContent = label;
            link.classList.remove('is-export-success', 'is-export-error');
            if (className) link.classList.add(className);
        }

        function downloadExport(link) {
            if (link.getAttribute('aria-busy') === 'true') return;
            var defaultLabel = link.dataset.defaultLabel || link.textContent.trim();
            link.dataset.defaultLabel = defaultLabel;
            link.setAttribute('aria-busy', 'true');
            link.setAttribute('aria-live', 'polite');
            setExportState(link, 'Preparing...', '');

            fetch(exportUrl(link), { credentials: 'same-origin' })
                .then(function (response) {
                    if (!response.ok) throw new Error('Export failed with status ' + response.status);
                    return response.blob().then(function (blob) {
                        return { blob: blob, filename: exportFilename(response, link) };
                    });
                })
                .then(function (download) {
                    var objectUrl = URL.createObjectURL(download.blob);
                    var anchor = document.createElement('a');
                    anchor.href = objectUrl;
                    anchor.download = download.filename;
                    document.body.appendChild(anchor);
                    anchor.click();
                    anchor.remove();
                    setTimeout(function () { URL.revokeObjectURL(objectUrl); }, 0);
                    setExportState(link, 'Downloaded', 'is-export-success');
                    setTimeout(function () {
                        setExportState(link, defaultLabel, '');
                    }, 1800);
                })
                .catch(function (error) {
                    console.error('Unable to download report.', error);
                    setExportState(link, 'Try again', 'is-export-error');
                    setTimeout(function () {
                        setExportState(link, defaultLabel, '');
                    }, 3000);
                })
                .finally(function () {
                    link.removeAttribute('aria-busy');
                });
        }

        // Analytics exports mirror the live filter state; intelligence exports
        // retain their own unfiltered URLs.
        document.querySelectorAll('[data-export-download]').forEach(function (link) {
            link.dataset.exportReady = 'true';
            link.addEventListener('click', function (event) {
                event.preventDefault();
                downloadExport(link);
            });
        });

        syncCustomVisibility();
    })();

    var customMonthInput = document.querySelector('input[name="custom_month"]');
    if (customMonthInput) {
        customMonthInput.addEventListener('change', function () {
            var form = customMonthInput.closest('form');
            if (form) {
                // Remove range parameter to avoid conflicts
                var formData = new FormData(form);
                formData.delete('range');
                var params = new URLSearchParams(formData);
                window.location.search = params.toString();
            }
        });
    }

    var urgencyData = config.urgency.data;
    if (window.Chart && total(urgencyData) > 0) {
        new Chart(document.getElementById('urgencyChart'), {
            type: 'doughnut',
            data: {
                labels: config.urgency.labels,
                datasets: [{ data: urgencyData, backgroundColor: config.urgency.colors, borderColor: '#fff', borderWidth: 8, spacing: 2 }]
            },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, cutout: '58%' }
        });
    }

    var dueData = config.dueDate.data;
    var dueEl = document.getElementById('dueDateChart');
    if (window.Chart && dueEl && total(dueData) > 0) {
        new Chart(dueEl, {
            type: 'doughnut',
            data: {
                labels: config.dueDate.labels,
                datasets: [{ data: dueData, backgroundColor: config.dueDate.colors, borderColor: '#fff', borderWidth: 7, spacing: 1 }]
            },
            options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, cutout: '62%' }
        });
    }

    var monthlyLabels = config.monthly.labels;
    var monthlyData = config.monthly.data;
    var monthlyEl = document.getElementById('monthlyChart');
    var monthlyHasData = config.monthly.hasData;
    if (window.Chart && monthlyEl && monthlyLabels.length && monthlyHasData) {
        var monthlyCtx = monthlyEl.getContext('2d');
        var overviewGradient = monthlyCtx.createLinearGradient(0, 0, 0, monthlyEl.offsetHeight || 260);
        overviewGradient.addColorStop(0, 'rgba(37, 70, 111, .30)');
        overviewGradient.addColorStop(.62, 'rgba(56, 189, 248, .12)');
        overviewGradient.addColorStop(1, 'rgba(255, 255, 255, 0)');
        var maxMonthly = Math.max.apply(null, monthlyData.map(function (item) { return item === null ? 0 : Number(item || 0); }));
        var hasSingleOverviewPoint = monthlyLabels.length === 1;
        new Chart(monthlyEl, {
            type: 'line',
            data: {
                labels: monthlyLabels,
                datasets: [{
                    label: 'Shipments',
                    data: monthlyData,
                    borderColor: '#25466F',
                    backgroundColor: overviewGradient,
                    fill: true,
                    tension: .36,
                    spanGaps: false,
                    borderWidth: hasSingleOverviewPoint ? 0 : 3,
                    pointRadius: hasSingleOverviewPoint ? 7 : 4.5,
                    pointHoverRadius: hasSingleOverviewPoint ? 9 : 7,
                    pointBackgroundColor: '#25466F',
                    pointBorderColor: '#fff',
                    pointBorderWidth: 2.5,
                    pointHitRadius: 14
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                layout: { padding: { left: hasSingleOverviewPoint ? 28 : 0, right: hasSingleOverviewPoint ? 28 : 0 } },
                interaction: { mode: 'index', intersect: false },
                onClick: function(event, elements) {
                    if (!elements.length) return;
                    var idx = elements[0].index;
                    if (this.data.datasets[0].data[idx] === null) return;
                    var label = this.data.labels[idx];
                    var base = config.urls.shipmentRecords;
                    if (/^\d{4}$/.test(label)) {
                        window.location.href = base + '?date_from=' + label + '-01-01&date_to=' + label + '-12-31';
                        return;
                    }
                    // Parse "Mon YYYY" (default monthly view e.g. "Jan 2026")
                    var parsed = new Date(label + ' 01');
                    if (!isNaN(parsed.getTime())) {
                        var y = parsed.getFullYear();
                        var m = parsed.getMonth();
                        var lastDay = new Date(y, m + 1, 0).getDate();
                        var pad = function(n) { return String(n).padStart(2,'0'); };
                        window.location.href = base + '?date_from=' + y + '-' + pad(m+1) + '-01&date_to=' + y + '-' + pad(m+1) + '-' + pad(lastDay);
                    } else {
                        window.location.href = base;
                    }
                },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        displayColors: false,
                        backgroundColor: '#172033',
                        titleColor: '#fff',
                        bodyColor: '#fff',
                        padding: 12,
                        cornerRadius: 6,
                        callbacks: {
                            label: function (context) {
                                if (context.parsed.y === null) return 'No recorded data';
                                return 'Shipments: ' + context.parsed.y;
                            }
                        }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        suggestedMax: maxMonthly < 5 ? 5 : undefined,
                        border: { display: false },
                        grid: { color: '#E4EBF3', drawTicks: false },
                        ticks: { color: '#334155', font: { size: 12, weight: '700' }, precision: 0, padding: 10 }
                    },
                    x: {
                        offset: hasSingleOverviewPoint,
                        border: { color: '#CCD6E2' },
                        grid: { display: false },
                        ticks: { color: '#334155', font: { size: 12, weight: '700' }, maxRotation: 0, autoSkipPadding: 18 }
                    }
                }
            }
        });
    }

    var costTypeEl = document.getElementById('costTypeChart');
    if (window.Chart && costTypeEl) {
        var costTypeKeys = config.cost.keys;
        new Chart(costTypeEl, {
            type: 'bar',
            data: {
                labels: config.cost.labels,
                datasets: [{
                    label: 'Avg Total Landed Cost (₱)',
                    data: config.cost.data,
                    backgroundColor: config.cost.colors,
                    borderRadius: 6,
                    borderSkipped: false,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                onClick: function(event, elements) {
                    if (!elements.length) return;
                    var key = costTypeKeys[elements[0].index] || '';
                    window.location.href = config.urls.shipmentRecords + '?stype=' + key;
                },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        displayColors: false,
                        backgroundColor: '#172033',
                        titleColor: '#fff',
                        bodyColor: '#fff',
                        padding: 12,
                        cornerRadius: 6,
                        callbacks: {
                            label: function(ctx) {
                                return '₱ ' + Number(ctx.parsed.y).toLocaleString('en-PH', {minimumFractionDigits: 2, maximumFractionDigits: 2});
                            }
                        }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        border: { display: false },
                        grid: { color: '#E9EEF4' },
                        ticks: {
                            color: '#334155',
                            font: { size: 11, weight: '700' },
                            callback: function(val) {
                                return val >= 1000000 ? '₱' + (val/1000000).toFixed(1) + 'M'
                                     : val >= 1000    ? '₱' + (val/1000).toFixed(0) + 'K'
                                     : '₱' + val;
                            }
                        }
                    },
                    x: {
                        grid: { display: false },
                        ticks: { color: '#334155', font: { size: 12, weight: '700' } }
                    }
                }
            }
        });
    }

    var wmcdaEl = document.getElementById('wmcdaChart');
    if (window.Chart && wmcdaEl) {
        var wmcdaKeys = config.wmcda.keys;
        new Chart(wmcdaEl, {
            type: 'bar',
            data: {
                labels: config.wmcda.labels,
                datasets: [{ data: config.wmcda.data, backgroundColor: config.wmcda.colors, borderRadius: 4 }]
            },
            options: {
                maintainAspectRatio: false,
                onClick: function(event, elements) {
                    if (!elements.length) return;
                    var idx = elements[0].index;
                    var rec = wmcdaKeys.length ? wmcdaKeys[idx] : this.data.labels[idx].toLowerCase();
                    window.location.href = config.urls.shipmentRecords + '?mcda_rec=' + rec;
                },
                plugins: { legend: { display: false } },
                scales: {
                    y: { beginAtZero: true, grid: { color: '#E9EEF4' }, ticks: { color: '#111827', font: { size: 11 }, precision: 0 } },
                    x: { grid: { display: false }, ticks: { color: '#111827', font: { size: 11, weight: '700' } } }
                }
            }
        });
    }
})();

// ─── Live status polling (every 60 s) ────────────────────────────────────────
(function () {
    var POLL_URL = config.urls.statusCounts + window.location.search;
    var INTERVAL  = 60000; // 60 seconds

    function pad2(n) { return String(n).padStart(2, '0'); }

    function fmt(d) {
        return d.getHours() + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds());
    }

    function setLiveState(state, label, detail) {
        var indicator = document.getElementById('live-indicator');
        var labelEl = document.getElementById('live-label');
        var tsEl = document.getElementById('live-ts');
        if (indicator) indicator.className = 'live-state ' + state;
        if (labelEl) labelEl.textContent = label;
        if (tsEl) tsEl.textContent = detail || '';
    }

    function refresh() {
        fetch(POLL_URL, { credentials: 'same-origin' })
            .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
            .then(function (data) {
                // Update total KPI
                var kpiEl = document.getElementById('kpi-total');
                if (kpiEl) kpiEl.textContent = data.total;

                // Update each status count cell
                var cells = document.querySelectorAll('[data-live]');
                cells.forEach(function (el) {
                    var key   = el.getAttribute('data-live');
                    var entry = data.counts[key];
                    if (entry !== undefined) {
                        var n = typeof entry === 'object' ? entry.count : entry;
                        el.textContent = pad2(n);
                    }
                });

                // Update timestamp
                setLiveState('is-online', 'Live', 'Updated ' + fmt(new Date()));
            })
            .catch(function () {
                setLiveState('is-stale', 'Update paused', 'Showing last loaded data');
            });
    }

    refresh();
    setInterval(function () {
        if (document.visibilityState === 'visible') refresh();
    }, INTERVAL);
    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'visible') refresh();
    });
})();
})();
