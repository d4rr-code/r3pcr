(function() {
    'use strict';

    /* ── Urgency card selection ────────────────────────────────────────────── */
    var URG_CLS = { standard:'s-std', priority:'s-pri', urgent:'s-urg', rush:'s-rush' };
    window.selUrg = function(radio) {
        ['standard','priority','urgent','rush'].forEach(function(v) {
            var el = document.getElementById('urg-' + v);
            if (el) el.className = 'urg-card';
        });
        var selected = document.getElementById('urg-' + radio.value);
        if (selected) selected.classList.add(URG_CLS[radio.value]);
    };

    /* ── Shipping type card selection ──────────────────────────────────────── */
    window.selShip = function(radio) {
        ['air','lcl','fcl','land'].forEach(function(v) {
            var el = document.getElementById('ship-' + v);
            if (el) el.className = 'ship-card';
        });
        var selected = document.getElementById('ship-' + radio.value);
        if (selected) selected.classList.add('s-sel');
    };

    /* ── Upload zone helpers ───────────────────────────────────────────────── */
    window.trigFile = function(inputId) {
        if (event) event.stopPropagation();
        var input = document.getElementById(inputId);
        if (input) input.click();
    };

    window.showFn = function(input, zoneId) {
        var names = Array.from(input.files).map(function(f) { return f.name; }).join(', ');
        var el = document.getElementById('fn-' + zoneId);
        if (el) el.textContent = names;
    };

    window.dzOver = function(e, zoneId) {
        e.preventDefault();
        var zone = document.getElementById(zoneId);
        if (zone) zone.classList.add('dz-over');
    };

    window.dzLeave = function(zoneId) {
        var zone = document.getElementById(zoneId);
        if (zone) zone.classList.remove('dz-over');
    };

    window.dzDrop = function(e, zoneId, inputId) {
        e.preventDefault();
        var zone = document.getElementById(zoneId);
        if (zone) zone.classList.remove('dz-over');
        
        var input = document.getElementById(inputId);
        if (!input) return;
        
        try {
            var dt = new DataTransfer();
            Array.from(e.dataTransfer.files).forEach(function(f) { dt.items.add(f); });
            input.files = dt.files;
            showFn(input, zoneId);
        } catch(err) {
            console.warn('Drag-drop not supported in this browser:', err);
        }
    };

    /* ── Currency label update ─────────────────────────────────────────────── */
    window.updateCurrencyLabels = function(code) {
        document.querySelectorAll('.invoice-cur-label').forEach(function(el) {
            el.textContent = code;
        });
    };

})();
