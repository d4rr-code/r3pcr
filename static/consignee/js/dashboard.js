(function () {
    var configEl = document.getElementById('dashboard-config');
    var config = {};

    if (configEl) {
        try {
            config = JSON.parse(configEl.textContent);
        } catch (e) {
            console.warn('Dashboard config parse error', e);
        }
    }

    var shipmentCanvas = document.getElementById('shipmentChart');
    var shipmentChart = null;

    function makeGradient(ctx) {
        var g = ctx.createLinearGradient(0, 0, 0, 200);
        g.addColorStop(0, 'rgba(27,51,88,0.18)');
        g.addColorStop(1, 'rgba(27,51,88,0.00)');
        return g;
    }

    if (shipmentCanvas && window.Chart) {
        Chart.defaults.font.family = 'Poppins, sans-serif';
        Chart.defaults.font.size = 11;
        Chart.defaults.font.weight = '500';

        var shipmentCtx = shipmentCanvas.getContext('2d');

        shipmentChart = new Chart(shipmentCtx, {
            type: 'line',
            data: {
                labels: config.monthlyLabels || [],
                datasets: [{
                    label: 'Shipment Overview',
                    data: config.monthlyData || [],
                    borderColor: '#1B3358',
                    borderWidth: 2,
                    backgroundColor: makeGradient(shipmentCtx),
                    pointBackgroundColor: '#1B3358',
                    pointBorderColor: '#ffffff',
                    pointBorderWidth: 2,
                    pointRadius: 3,
                    pointHoverRadius: 5,
                    pointHoverBackgroundColor: '#1B3358',
                    pointHoverBorderColor: '#ffffff',
                    pointHoverBorderWidth: 2,
                    tension: 0.35,
                    fill: true,
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: '#1B3358',
                        titleColor: '#fff',
                        bodyColor: '#fff',
                        padding: 10,
                        cornerRadius: 8,
                        displayColors: false,
                        usePointStyle: true,
                        callbacks: {
                            title: function (items) { return items[0].label; },
                            label: function (item) {
                                return item.raw + ' total shipment' + (item.raw !== 1 ? 's' : '');
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        grid: { color: '#F3F4F6' },
                        ticks: {
                            font: { family: "'Poppins', sans-serif", size: 11, weight: '500' },
                            color: '#9CA3AF',
                            maxRotation: 0
                        }
                    },
                    y: {
                        grid: { color: '#F3F4F6' },
                        ticks: {
                            font: { family: "'Poppins', sans-serif", size: 11, weight: '500' },
                            color: '#9CA3AF',
                            precision: 0
                        },
                        beginAtZero: true
                    }
                }
            }
        });
    }

    var todayIso = new Date().toISOString().slice(0, 10);
    var thirtyDaysAgo = new Date();
    thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);

    function toIsoDate(d) {
        var year = d.getFullYear();
        var month = String(d.getMonth() + 1).padStart(2, '0');
        var day = String(d.getDate()).padStart(2, '0');
        return year + '-' + month + '-' + day;
    }

    var chartFilters = {
        startYear: null,
        startMonth: null,
        endYear: null,
        endMonth: null,
        groupBy: 'day'
    };

    var chartDataCache = {};
    var urgencyDataCache = {};
    var shippingDataCache = {};
    var inFlightChartRequest = null;
    var inFlightUrgencyRequest = null;
    var inFlightShippingRequest = null;

    function getChartCacheKey(filters) {
        return [
            filters.startYear,
            pad2(filters.startMonth),
            filters.endYear,
            pad2(filters.endMonth),
            filters.groupBy
        ].join('|');
    }

    function applyChartData(payload) {
        shipmentChart.data.labels = payload.labels || [];
        shipmentChart.data.datasets[0].data = payload.data || [];
        shipmentChart.data.datasets[0].backgroundColor = makeGradient(shipmentChart.ctx);
        shipmentChart.update();
    }

    function getUrgencyCacheKey(year, month) {
        return [year, month].join('|');
    }

    function getShippingCacheKey(year, month) {
        return [year, month].join('|');
    }

    window.fetchChartData = function () {
        if (!config.chartDataUrl || !shipmentChart) return;

        var key = getChartCacheKey(chartFilters);
        if (chartDataCache[key]) {
            applyChartData(chartDataCache[key]);
            return;
        }

        var url = new URL(config.chartDataUrl, window.location.origin);
        url.searchParams.set('start_year', chartFilters.startYear);
        url.searchParams.set('start_month', pad2(chartFilters.startMonth));
        url.searchParams.set('end_year', chartFilters.endYear);
        url.searchParams.set('end_month', pad2(chartFilters.endMonth));
        url.searchParams.set('group_by', chartFilters.groupBy);

        if (inFlightChartRequest && typeof inFlightChartRequest.abort === 'function') {
            inFlightChartRequest.abort();
        }

        inFlightChartRequest = new AbortController();

        fetch(url.toString(), { signal: inFlightChartRequest.signal })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                chartDataCache[key] = { labels: d.labels || [], data: d.data || [] };
                applyChartData(chartDataCache[key]);
            })
            .catch(function (e) {
                if (e && e.name === 'AbortError') return;
                console.warn('Chart fetch error', e);
            });
    };

    var urgencyCanvas = document.getElementById('urgencyChart');
    var urgencyData = config.urgencyData || [];
    var urgencyChart = null;

    if (urgencyCanvas && window.Chart) {
        var gaugeOrder = ['Standard', 'Rush / Time-Critical', 'Priority', 'Urgent'];
        var urgencyMap = {};
        urgencyData.forEach(function (item) {
            urgencyMap[(item.label || '').trim().toLowerCase()] = item;
        });

        var orderedUrgency = gaugeOrder
            .map(function (label) {
                return urgencyMap[label.toLowerCase()] || {
                    label: label,
                    count: 0,
                    color: (label === 'Standard' ? '#2F7DE1'
                        : label === 'Rush / Time-Critical' ? '#E30000'
                        : label === 'Priority' ? '#F5D328'
                        : '#F97300')
                };
            });

        var hasActualData = orderedUrgency.some(function (d) { return Number(d.count) > 0; });

        if (!hasActualData) {
            orderedUrgency = [{ label: 'None', count: 1, color: '#E5E7EB' }];
        }

        urgencyChart = new Chart(urgencyCanvas.getContext('2d'), {
            type: 'doughnut',
            data: {
                labels: orderedUrgency.map(function (d) { return d.label; }),
                datasets: [{
                    data: orderedUrgency.map(function (d) { return d.count; }),
                    backgroundColor: orderedUrgency.map(function (d) { return d.color; }),
                    borderWidth: 6,
                    borderColor: '#ffffff',
                    borderRadius: 10,
                    spacing: 4,
                    hoverOffset: 0,
                    hoverBorderWidth: 6,
                    hoverBorderColor: '#ffffff',
                    offset: 0,
                }]
            },
            options: {
                cutout: '58%',
                responsive: false,
                rotation: -130,
                circumference: 260,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        displayColors: false,
                        backgroundColor: 'rgba(17, 24, 39, 0.92)',
                        titleColor: '#FFFFFF',
                        bodyColor: '#FFFFFF',
                        borderWidth: 0,
                        cornerRadius: 8,
                        padding: 8,
                        callbacks: {
                            title: function () { return ''; },
                            label: function (c) { return c.label + ': ' + c.raw; }
                        }
                    }
                },
                animation: {
                    animateRotate: true,
                    duration: 800
                }
            }
        });
    }

    function urgencyColorByLabel(label) {
        var key = (label || '').trim().toLowerCase();
        if (key === 'standard' || key === 'normal') return '#2F7DE1';
        if (key === 'rush' || key === 'rush / time-critical' || key === 'time-critical' || key === 'time critical') return '#E30000';
        if (key === 'priority') return '#F5D328';
        if (key === 'urgent') return '#F97300';
        return '#9CA3AF';
    }

    function sortUrgencyForGauge(data) {
        var gaugeOrder = ['Standard', 'Rush', 'Priority', 'Urgent'];
        var urgencyMap = {};
        (data || []).forEach(function (item) {
            var normalized = (item.label || item.urgency || '').trim().toLowerCase();
            var canonical = normalized;
            if (normalized === 'rush / time-critical' || normalized === 'time-critical' || normalized === 'time critical') {
                canonical = 'rush';
            } else if (normalized === 'normal') {
                canonical = 'standard';
            }
            urgencyMap[canonical] = item;
        });

        var orderedUrgency = gaugeOrder.map(function (label) {
            var key = label.toLowerCase();
            var source = urgencyMap[key] || {};
            var count = Number(source.count) || 0;
            return {
                label: label,
                count: count,
                color: urgencyColorByLabel(label)
            };
        });

        var hasActualData = orderedUrgency.some(function (d) { return Number(d.count) > 0; });
        if (!hasActualData) return [{ label: 'None', count: 1, color: '#E5E7EB' }];
        return orderedUrgency;
    }

    function applyUrgencyData(data) {
        if (!urgencyChart) return;
        var ordered = sortUrgencyForGauge(data);
        urgencyChart.data.labels = ordered.map(function (d) { return d.label; });
        urgencyChart.data.datasets[0].data = ordered.map(function (d) { return d.count; });
        urgencyChart.data.datasets[0].backgroundColor = ordered.map(function (d) { return d.color; });
        urgencyChart.update();

        var legendMap = {};
        ordered.forEach(function (d) {
            var k = (d.label || '').toLowerCase();
            legendMap[k] = Number(d.count) || 0;
            if (k === 'rush') legendMap['rush / time-critical'] = Number(d.count) || 0;
            if (k === 'standard') legendMap['normal'] = Number(d.count) || 0;
        });

        document.querySelectorAll('.urgency-list-item').forEach(function (row) {
            var labelEl = row.querySelector('.urgency-label');
            var countEl = row.querySelector('.urgency-count');
            if (!labelEl || !countEl) return;

            var key = (labelEl.textContent || '').trim().toLowerCase();
            countEl.textContent = String(legendMap[key] || 0);
        });
    }

    function applyShippingDataByMonth(data, total) {
        var shippingTotalEl = document.getElementById('shipping-total-value');
        if (shippingTotalEl) shippingTotalEl.textContent = String(Number(total) || 0);

        var shippingBarEl = document.getElementById('shipping-type-bar');
        var shippingGridEl = document.getElementById('shipping-type-grid');
        if (!shippingBarEl || !shippingGridEl) return;

        var incoming = Array.isArray(data) ? data : [];
        var safeTotal = Number(total) || 0;

        var canonicalOrder = [
            { key: 'air', label: 'Air Freight' },
            { key: 'fcl', label: 'FCL - Full Container Load' },
            { key: 'lcl', label: 'LCL - Less Container Load' }
        ];

        var incomingMap = {};
        incoming.forEach(function (item) {
            var key = (item && item.shipment_type ? item.shipment_type : 'other').toLowerCase();
            if (!incomingMap[key]) incomingMap[key] = { count: 0, label: item.label || '' };
            incomingMap[key].count += Number(item.count) || 0;
            if (!incomingMap[key].label && item.label) incomingMap[key].label = item.label;
        });

        var normalized = canonicalOrder.map(function (def) {
            var source = incomingMap[def.key] || { count: 0, label: def.label };
            return {
                shipment_type: def.key,
                label: source.label || def.label,
                count: Number(source.count) || 0
            };
        });

        var coloredSegments = normalized.filter(function (item) { return (Number(item.count) || 0) > 0; });
        if (!coloredSegments.length) {
            shippingBarEl.innerHTML = '<div class="ship-type-segment ship-type-empty" data-count="1" style="flex-grow:1;"></div>';
        } else {
            shippingBarEl.innerHTML = coloredSegments.map(function (item) {
                var key = item.shipment_type;
                var count = Number(item.count) || 0;
                return '<div class="ship-type-segment ship-type-' + key + '" data-count="' + count + '" style="flex-grow:' + count + ';"></div>';
            }).join('');
        }

        shippingGridEl.innerHTML = normalized.map(function (item) {
            var key = item.shipment_type;
            var label = item.label || 'Unknown';
            var count = Number(item.count) || 0;
            var percent = safeTotal > 0 ? Math.round((count / safeTotal) * 100) : 0;

            return '' +
                '<div class="ship-type-item" data-shipment-type="' + key + '" data-label="' + label + '">' +
                    '<div class="ship-type-label">' +
                        '<span class="ship-type-dot ship-type-' + key + '"></span>' +
                        '<span class="ship-type-text">' + label + ' (' + percent + '%)</span>' +
                    '</div>' +
                    '<p class="ship-type-count">' + count + '</p>' +
                '</div>';
        }).join('');
    }

    function fetchShippingDataByMonth(year, month) {
        if (!config.chartDataUrl) return;
        var key = getShippingCacheKey(year, month);

        if (shippingDataCache[key]) {
            applyShippingDataByMonth(
                shippingDataCache[key].breakdown || [],
                shippingDataCache[key].total || 0
            );
            return;
        }

        var url = new URL(config.chartDataUrl, window.location.origin);
        url.searchParams.set('start_year', year);
        url.searchParams.set('start_month', pad2(month));
        url.searchParams.set('end_year', year);
        url.searchParams.set('end_month', pad2(month));
        url.searchParams.set('group_by', 'month');

        if (inFlightShippingRequest && typeof inFlightShippingRequest.abort === 'function') {
            inFlightShippingRequest.abort();
        }
        inFlightShippingRequest = new AbortController();

        fetch(url.toString(), { signal: inFlightShippingRequest.signal })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                shippingDataCache[key] = {
                    breakdown: d.shipping_breakdown || [],
                    total: Number(d.shipping_total) || 0
                };
                applyShippingDataByMonth(
                    shippingDataCache[key].breakdown,
                    shippingDataCache[key].total
                );
            })
            .catch(function (e) {
                if (e && e.name === 'AbortError') return;
                console.warn('Shipping fetch error', e);
            });
    }

    function fetchUrgencyDataByMonth(year, month) {
        if (!config.chartDataUrl || !urgencyChart) return;
        var key = getUrgencyCacheKey(year, month);
        if (urgencyDataCache[key]) {
            applyUrgencyData(urgencyDataCache[key].breakdown || []);
            var cachedTotalEl = document.querySelector('.urgency-center-value');
            if (cachedTotalEl) cachedTotalEl.textContent = String(urgencyDataCache[key].total || 0);
            return;
        }

        var url = new URL(config.chartDataUrl, window.location.origin);
        url.searchParams.set('start_year', year);
        url.searchParams.set('start_month', pad2(month));
        url.searchParams.set('end_year', year);
        url.searchParams.set('end_month', pad2(month));
        url.searchParams.set('group_by', 'month');

        if (inFlightUrgencyRequest && typeof inFlightUrgencyRequest.abort === 'function') {
            inFlightUrgencyRequest.abort();
        }
        inFlightUrgencyRequest = new AbortController();

        fetch(url.toString(), { signal: inFlightUrgencyRequest.signal })
            .then(function (r) { return r.json(); })
            .then(function (d) {
                urgencyDataCache[key] = {
                    breakdown: d.urgency_breakdown || [],
                    total: Number(d.total) || 0
                };
                applyUrgencyData(urgencyDataCache[key].breakdown);

                var totalEl = document.querySelector('.urgency-center-value');
                if (totalEl) totalEl.textContent = String(urgencyDataCache[key].total);
            })
            .catch(function (e) {
                if (e && e.name === 'AbortError') return;
                console.warn('Urgency fetch error', e);
            });
    }

    var months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    var today = new Date();
    var chartStartTrigger = document.getElementById('chart-start-trigger');
    var chartEndTrigger = document.getElementById('chart-end-trigger');
    var urgencyTrigger = document.getElementById('mpbtn-urgency');
    var shippingTrigger = document.getElementById('mpbtn-shipping');
    var chartStartLabel = document.getElementById('chart-start-label');
    var chartEndLabel = document.getElementById('chart-end-label');
    var monthPicker = document.getElementById('month-picker');
    var monthGrid = document.getElementById('month-grid');
    var yearGrid = document.getElementById('year-grid');
    var pickerHeaderLabel = document.getElementById('picker-header-label');
    var pickerPrev = document.getElementById('picker-prev');
    var pickerNext = document.getElementById('picker-next');

    var chartPickerState = {
        mode: 'month',
        target: null,
        viewYear: today.getFullYear(),
        selectedStartYear: today.getFullYear(),
        selectedStartMonth: Math.max(1, today.getMonth() - 4),
        selectedEndYear: today.getFullYear(),
        selectedEndMonth: today.getMonth() + 1,
        selectedUrgencyYear: today.getFullYear(),
        selectedUrgencyMonth: today.getMonth() + 1,
        yearPageStart: today.getFullYear() - (today.getFullYear() % 10)
    };

    function deriveRecentYearRange(startMonth, endMonth) {
        var currentYear = today.getFullYear();
        var sMonth = Number(startMonth);
        var eMonth = Number(endMonth);

        if (!Number.isFinite(sMonth) || sMonth < 1 || sMonth > 12) sMonth = 1;
        if (!Number.isFinite(eMonth) || eMonth < 1 || eMonth > 12) eMonth = 12;

        var startYear = currentYear;
        var endYear = currentYear;

        if (sMonth > eMonth) {
            startYear = currentYear - 1;
            endYear = currentYear;
        }

        return {
            startYear: startYear,
            endYear: endYear
        };
    }

    function pad2(n) {
        return String(n).padStart(2, '0');
    }

    function updateChartFilterPayload() {
        var rangeYears = deriveRecentYearRange(
            chartPickerState.selectedStartMonth,
            chartPickerState.selectedEndMonth
        );

        chartFilters.startYear = rangeYears.startYear;
        chartFilters.startMonth = Number(chartPickerState.selectedStartMonth);
        chartFilters.endYear = rangeYears.endYear;
        chartFilters.endMonth = Number(chartPickerState.selectedEndMonth);
    }

    function monthYearLabel(year, month) {
        return months[month - 1] + ' ' + year;
    }

    function syncChartTriggerLabels() {
        if (chartStartLabel) chartStartLabel.textContent = monthYearLabel(chartPickerState.selectedStartYear, chartPickerState.selectedStartMonth);
        if (chartEndLabel) chartEndLabel.textContent = monthYearLabel(chartPickerState.selectedEndYear, chartPickerState.selectedEndMonth);
    }

    function normalizeSelectedRange() {
        var startKey = (chartPickerState.selectedStartYear * 100) + chartPickerState.selectedStartMonth;
        var endKey = (chartPickerState.selectedEndYear * 100) + chartPickerState.selectedEndMonth;
        if (startKey > endKey) {
            chartPickerState.selectedEndYear = chartPickerState.selectedStartYear;
            chartPickerState.selectedEndMonth = chartPickerState.selectedStartMonth;
        }
    }

    function setPickerPosition(triggerEl) {
        if (!monthPicker || !triggerEl) return;
        var rect = triggerEl.getBoundingClientRect();
        monthPicker.style.top = (rect.bottom + 8) + 'px';
        var left = rect.left;
        if (left + 360 > window.innerWidth) left = window.innerWidth - 370;
        if (left < 10) left = 10;
        monthPicker.style.left = left + 'px';
    }

    function renderChartMonthGrid() {
        if (!monthGrid || !pickerHeaderLabel) return;
        monthGrid.style.display = 'grid';
        if (yearGrid) yearGrid.style.display = 'none';

        var recentRangeYears = deriveRecentYearRange(
            chartPickerState.selectedStartMonth,
            chartPickerState.selectedEndMonth
        );
        pickerHeaderLabel.textContent = recentRangeYears.startYear === recentRangeYears.endYear
            ? String(recentRangeYears.endYear)
            : (recentRangeYears.startYear + '–' + recentRangeYears.endYear);

        monthGrid.innerHTML = '';

        months.forEach(function (m, idx) {
            var monthNum = idx + 1;
            var isActive = false;
            if (chartPickerState.target === 'start') {
                isActive = chartPickerState.selectedStartMonth === monthNum;
            } else if (chartPickerState.target === 'end') {
                isActive = chartPickerState.selectedEndMonth === monthNum;
            } else {
                isActive = chartPickerState.selectedUrgencyYear === chartPickerState.viewYear && chartPickerState.selectedUrgencyMonth === monthNum;
            }

            var b = document.createElement('button');
            b.type = 'button';
            b.textContent = m;
            b.className = 'picker-grid-btn' + (isActive ? ' active' : '');

            b.onclick = function () {
                if (chartPickerState.target === 'start') {
                    chartPickerState.selectedStartMonth = monthNum;
                } else if (chartPickerState.target === 'end') {
                    chartPickerState.selectedEndMonth = monthNum;
                } else {
                    chartPickerState.selectedUrgencyYear = chartPickerState.viewYear;
                    chartPickerState.selectedUrgencyMonth = monthNum;
                }

                normalizeSelectedRange();
                syncChartTriggerLabels();
                updateChartFilterPayload();
                monthPicker.style.display = 'none';

                if (chartPickerState.target === 'urgency') {
                    if (urgencyTrigger) {
                        var urgencyLabel = urgencyTrigger.querySelector('.mp-label');
                        if (urgencyLabel) urgencyLabel.textContent = monthYearLabel(chartPickerState.selectedUrgencyYear, chartPickerState.selectedUrgencyMonth);
                    }
                    fetchUrgencyDataByMonth(chartPickerState.selectedUrgencyYear, chartPickerState.selectedUrgencyMonth);
                } else if (chartPickerState.target === 'shipping') {
                    var shippingTrigger = document.getElementById('mpbtn-shipping');
                    if (shippingTrigger) {
                        var shippingLabel = shippingTrigger.querySelector('.mp-label');
                        if (shippingLabel) shippingLabel.textContent = monthYearLabel(chartPickerState.selectedUrgencyYear, chartPickerState.selectedUrgencyMonth);
                    }
                    fetchShippingDataByMonth(chartPickerState.selectedUrgencyYear, chartPickerState.selectedUrgencyMonth);
                } else {
                    window.fetchChartData();
                }

                chartPickerState.target = null;
            };

            monthGrid.appendChild(b);
        });
    }

    function renderChartYearGrid() {
        if (!yearGrid || !pickerHeaderLabel) return;
        if (monthGrid) monthGrid.style.display = 'none';
        yearGrid.style.display = 'grid';
        pickerHeaderLabel.textContent = chartPickerState.yearPageStart + '–' + (chartPickerState.yearPageStart + 9);
        yearGrid.innerHTML = '';

        for (var y = chartPickerState.yearPageStart; y < chartPickerState.yearPageStart + 12; y++) {
            var isActiveYear = false;
            if (chartPickerState.target === 'start') {
                isActiveYear = chartPickerState.selectedStartYear === y;
            } else if (chartPickerState.target === 'end') {
                isActiveYear = chartPickerState.selectedEndYear === y;
            } else {
                isActiveYear = chartPickerState.selectedUrgencyYear === y;
            }

            var by = document.createElement('button');
            by.type = 'button';
            by.textContent = y;
            by.className = 'picker-grid-btn' + (isActiveYear ? ' active' : '') + (y > (today.getFullYear() + 5) ? ' disabled' : '');

            by.onclick = (function (yearValue) {
                return function () {
                    chartPickerState.viewYear = yearValue;
                    if (chartPickerState.target === 'start') {
                        chartPickerState.selectedStartYear = yearValue;
                    } else if (chartPickerState.target === 'end') {
                        chartPickerState.selectedEndYear = yearValue;
                    } else {
                        chartPickerState.selectedUrgencyYear = yearValue;
                    }
                    normalizeSelectedRange();
                    chartPickerState.mode = 'month';
                    renderChartMonthGrid();
                    syncChartTriggerLabels();
                    updateChartFilterPayload();

                    if (chartPickerState.target === 'urgency') {
                        if (urgencyTrigger) {
                            var urgencyLabel = urgencyTrigger.querySelector('.mp-label');
                            if (urgencyLabel) urgencyLabel.textContent = monthYearLabel(chartPickerState.selectedUrgencyYear, chartPickerState.selectedUrgencyMonth);
                        }
                        fetchUrgencyDataByMonth(chartPickerState.selectedUrgencyYear, chartPickerState.selectedUrgencyMonth);
                    } else if (chartPickerState.target === 'shipping') {
                        var shippingTrigger = document.getElementById('mpbtn-shipping');
                        if (shippingTrigger) {
                            var shippingLabel = shippingTrigger.querySelector('.mp-label');
                            if (shippingLabel) shippingLabel.textContent = monthYearLabel(chartPickerState.selectedUrgencyYear, chartPickerState.selectedUrgencyMonth);
                        }
                        fetchShippingDataByMonth(chartPickerState.selectedUrgencyYear, chartPickerState.selectedUrgencyMonth);
                    } else {
                        window.fetchChartData();
                    }
                };
            })(y);

            yearGrid.appendChild(by);
        }
    }

    function openChartPicker(target, triggerEl) {
        if (!monthPicker) return;
        if (monthPicker.style.display !== 'none' && chartPickerState.target === target) {
            monthPicker.style.display = 'none';
            chartPickerState.target = null;
            return;
        }

        chartPickerState.target = target;
        chartPickerState.mode = 'month';

        if (target === 'start' || target === 'end') {
            if (yearGrid) yearGrid.style.display = 'none';
            if (pickerHeaderLabel) pickerHeaderLabel.style.pointerEvents = 'none';
        } else {
            if (pickerHeaderLabel) pickerHeaderLabel.style.pointerEvents = 'auto';
            chartPickerState.viewYear = chartPickerState.selectedUrgencyYear;
            chartPickerState.yearPageStart = chartPickerState.viewYear - (chartPickerState.viewYear % 10);
        }

        setPickerPosition(triggerEl);
        renderChartMonthGrid();
        monthPicker.style.display = 'block';
    }

    if (chartStartTrigger) {
        chartStartTrigger.addEventListener('click', function () {
            openChartPicker('start', chartStartTrigger);
        });
    }

    if (chartEndTrigger) {
        chartEndTrigger.addEventListener('click', function () {
            openChartPicker('end', chartEndTrigger);
        });
    }

    if (pickerHeaderLabel) {
        pickerHeaderLabel.style.cursor = 'pointer';
        pickerHeaderLabel.addEventListener('click', function () {
            if (chartPickerState.target === 'start' || chartPickerState.target === 'end') return;
            chartPickerState.mode = chartPickerState.mode === 'month' ? 'year' : 'month';
            if (chartPickerState.mode === 'year') renderChartYearGrid();
            else renderChartMonthGrid();
        });
    }

    if (pickerPrev) {
        pickerPrev.addEventListener('click', function () {
            if (chartPickerState.mode === 'year') {
                chartPickerState.yearPageStart -= 10;
                renderChartYearGrid();
            } else {
                chartPickerState.viewYear -= 1;
                renderChartMonthGrid();
            }
        });
    }

    if (pickerNext) {
        pickerNext.addEventListener('click', function () {
            if (chartPickerState.mode === 'year') {
                chartPickerState.yearPageStart += 10;
                renderChartYearGrid();
            } else {
                chartPickerState.viewYear += 1;
                renderChartMonthGrid();
            }
        });
    }

    chartPickerState.selectedEndYear = today.getFullYear();
    chartPickerState.selectedEndMonth = today.getMonth() + 1;
    var fromDate = new Date(today.getFullYear(), today.getMonth() - 5, 1);
    chartPickerState.selectedStartYear = fromDate.getFullYear();
    chartPickerState.selectedStartMonth = fromDate.getMonth() + 1;
    chartPickerState.viewYear = chartPickerState.selectedEndYear;
    chartPickerState.yearPageStart = chartPickerState.viewYear - (chartPickerState.viewYear % 10);

    syncChartTriggerLabels();
    updateChartFilterPayload();

    window.fetchChartData();

    document.querySelectorAll('.ship-type-segment[data-count]').forEach(function (segment) {
        var count = parseFloat(segment.dataset.count);
        segment.style.flexGrow = Number.isFinite(count) && count > 0 ? count : 1;
    });

    document.querySelectorAll('.chart-group-btn').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var selected = (btn.dataset.group || 'day').toLowerCase();
            chartFilters.groupBy = selected;

            document.querySelectorAll('.chart-group-btn').forEach(function (b) {
                b.classList.toggle('active', b === btn);
            });

            window.fetchChartData();
        });
    });

    document.addEventListener('click', function (e) {
        var picker = document.getElementById('month-picker');

        if (!picker || picker.style.display === 'none') return;
        if (picker.contains(e.target)) return;

        var activeTrigger = null;
        if (chartPickerState.target === 'start') activeTrigger = chartStartTrigger;
        else if (chartPickerState.target === 'end') activeTrigger = chartEndTrigger;
        else if (chartPickerState.target === 'urgency') activeTrigger = urgencyTrigger;
        else if (chartPickerState.target === 'shipping') activeTrigger = shippingTrigger;

        if (activeTrigger && activeTrigger.contains(e.target)) return;

        picker.style.display = 'none';
        chartPickerState.target = null;
    });

    if (urgencyTrigger) {
        urgencyTrigger.addEventListener('click', function () {
            openChartPicker('urgency', urgencyTrigger);
        });
    }

    if (shippingTrigger) {
        shippingTrigger.addEventListener('click', function () {
            openChartPicker('shipping', shippingTrigger);
        });
    }
})();
