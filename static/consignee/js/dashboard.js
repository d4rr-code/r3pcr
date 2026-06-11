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
                    pointBorderColor: '#fff',
                    pointBorderWidth: 2,
                    pointRadius: 4,
                    pointHoverRadius: 6,
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
                        callbacks: {
                            title: function (items) { return items[0].label; },
                            label: function (item) {
                                return item.raw + ' shipment' + (item.raw !== 1 ? 's' : '');
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        grid: { color: '#F3F4F6' },
                        ticks: {
                            font: { size: 11, family: 'Inter' },
                            color: '#9CA3AF',
                            maxRotation: 0
                        }
                    },
                    y: {
                        grid: { color: '#F3F4F6' },
                        ticks: {
                            font: { size: 11, family: 'Inter' },
                            color: '#9CA3AF',
                            precision: 0
                        },
                        beginAtZero: true
                    }
                }
            }
        });
    }

    window.fetchChartData = function (year, month) {
        if (!config.chartDataUrl || !shipmentChart) return;

        var url = new URL(config.chartDataUrl, window.location.origin);
        url.searchParams.set('year', year);
        url.searchParams.set('month', month);

        fetch(url.toString())
            .then(function (r) { return r.json(); })
            .then(function (d) {
                shipmentChart.data.labels = d.labels;
                shipmentChart.data.datasets[0].data = d.data;
                shipmentChart.data.datasets[0].backgroundColor = makeGradient(shipmentChart.ctx);
                shipmentChart.update();
            })
            .catch(function (e) { console.warn('Chart fetch error', e); });
    };

    var urgencyCanvas = document.getElementById('urgencyChart');
    var urgencyData = config.urgencyData || [];

    if (urgencyCanvas && window.Chart) {
        if (!urgencyData.length) {
            urgencyData = [{ label: 'None', count: 1, color: '#E5E7EB' }];
        }

        new Chart(urgencyCanvas.getContext('2d'), {
            type: 'doughnut',
            data: {
                labels: urgencyData.map(function (d) { return d.label; }),
                datasets: [{
                    data: urgencyData.map(function (d) { return d.count; }),
                    backgroundColor: urgencyData.map(function (d) { return d.color; }),
                    borderWidth: 2,
                    borderColor: '#fff',
                    hoverOffset: 4,
                }]
            },
            options: {
                cutout: '68%',
                responsive: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            label: function (c) { return c.label + ': ' + c.raw; }
                        }
                    }
                }
            }
        });
    }

    var months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    var today = new Date();
    var pickerState = {
        year: parseInt(config.selectedYear, 10) || config.currentYear || today.getFullYear(),
        month: (parseInt(config.selectedMonth, 10) || (today.getMonth() + 1)) - 1
    };
    if (pickerState.month < 0 || pickerState.month > 11) {
        pickerState.month = today.getMonth();
    }
    var currentPickerTarget = null;

    window.openMonthPicker = function (triggerId) {
        var btn = document.getElementById(triggerId);
        var picker = document.getElementById('month-picker');

        if (!btn || !picker) return;

        if (picker.style.display !== 'none' && currentPickerTarget === triggerId) {
            picker.style.display = 'none';
            currentPickerTarget = null;
            return;
        }

        currentPickerTarget = triggerId;

        var rect = btn.getBoundingClientRect();
        picker.style.top = (rect.bottom + 8) + 'px';

        var left = rect.left;
        if (left + 270 > window.innerWidth) {
            left = window.innerWidth - 278;
        }

        picker.style.left = left + 'px';
        picker.style.display = 'block';
        renderMonthGrid();
    };

    function renderMonthGrid() {
        var yearLabel = document.getElementById('picker-year-label');
        var grid = document.getElementById('month-grid');

        if (!yearLabel || !grid) return;

        yearLabel.textContent = pickerState.year;
        grid.innerHTML = '';

        months.forEach(function (m, i) {
            var b = document.createElement('button');
            var active = i === pickerState.month;

            b.textContent = m;
            b.style.cssText = [
                'padding:8px;border-radius:8px;border:none;cursor:pointer;',
                'font-family:Poppins,sans-serif;font-size:12px;',
                'font-weight:' + (active ? '700' : '500') + ';',
                'background:' + (active ? '#1B3358' : 'transparent') + ';',
                'color:' + (active ? '#fff' : '#374151') + ';',
            ].join('');

            b.onmouseover = function () {
                if (!active) b.style.background = '#F3F4F6';
            };

            b.onmouseout = function () {
                if (!active) b.style.background = 'transparent';
            };

            b.onclick = function () {
                pickerState.month = i;
                var selectedMonth = i + 1;
                var label = m + ' ' + pickerState.year;

                document.querySelectorAll('.mp-label').forEach(function (el) {
                    el.textContent = label;
                });

                var newUrl = new URL(window.location.href);
                newUrl.searchParams.set('year', pickerState.year);
                newUrl.searchParams.set('month', selectedMonth);

                document.getElementById('month-picker').style.display = 'none';
                currentPickerTarget = null;

                window.location.href = newUrl.toString();
            };

            grid.appendChild(b);
        });
    }

    window.pickerYear = function (dir) {
        pickerState.year += dir;
        renderMonthGrid();
    };

    document.querySelectorAll('[data-month-picker-trigger]').forEach(function (trigger) {
        trigger.addEventListener('click', function () {
            window.openMonthPicker(trigger.id);
        });
    });

    document.querySelectorAll('[data-picker-year]').forEach(function (button) {
        button.addEventListener('click', function () {
            window.pickerYear(parseInt(button.dataset.pickerYear, 10));
        });
    });

    document.querySelectorAll('.mp-label').forEach(function (el) {
        el.textContent = months[pickerState.month] + ' ' + pickerState.year;
    });

    window.fetchChartData(pickerState.year, pickerState.month + 1);

    document.querySelectorAll('.ship-type-segment[data-count]').forEach(function (segment) {
        var count = parseFloat(segment.dataset.count);
        segment.style.flexGrow = Number.isFinite(count) && count > 0 ? count : 1;
    });

    document.addEventListener('click', function (e) {
        var picker = document.getElementById('month-picker');

        if (!picker || picker.style.display === 'none') return;
        if (picker.contains(e.target)) return;

        if (currentPickerTarget) {
            var triggerBtn = document.getElementById(currentPickerTarget);
            if (triggerBtn && triggerBtn.contains(e.target)) return;
        }

        picker.style.display = 'none';
        currentPickerTarget = null;
    });
})();
