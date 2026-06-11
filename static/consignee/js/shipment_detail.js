/* ─────────────────────────────────────────────────────────────────
   Shipment Detail Page JavaScript
   ───────────────────────────────────────────────────────────────── */

(function () {
    'use strict';

    /* ───────────────────────────────────────────────────────────
       Initialize when DOM is ready
       ─────────────────────────────────────────────────────────── */

    document.addEventListener('DOMContentLoaded', function () {
        setupResubmitButton();
        setupRevisionButton();
        setupApprovalButton();
        setupAdvisoryBarWidths();
    });

    /* ───────────────────────────────────────────────────────────
       Advisory bar width setup
       ─────────────────────────────────────────────────────────── */

    function setupAdvisoryBarWidths() {
        var fills = document.querySelectorAll('.bar-fill[data-width]');
        fills.forEach(function(el) {
            var width = el.getAttribute('data-width');
            if (!width) return;
            el.style.width = width + '%';
        });
    }

    /* ───────────────────────────────────────────────────────────
       Resubmit Documents Button
       ─────────────────────────────────────────────────────────── */

    function setupResubmitButton() {
        var resubmitBtn = document.querySelector('button[data-action="resubmit-docs"]');
        if (!resubmitBtn) {
            // Fallback: find button near "Resubmit Documents" text
            var buttons = document.querySelectorAll('button[type="submit"]');
            for (var i = 0; i < buttons.length; i++) {
                if (buttons[i].textContent.indexOf('Resubmit') !== -1) {
                    resubmitBtn = buttons[i];
                    break;
                }
            }
        }

        if (resubmitBtn) {
            resubmitBtn.addEventListener('click', function (e) {
                if (!confirm('Resubmit corrected documents? Your declarant will be notified.')) {
                    e.preventDefault();
                }
            });
        }
    }

    /* ───────────────────────────────────────────────────────────
       Request Revision Button
       ─────────────────────────────────────────────────────────── */

    function setupRevisionButton() {
        var reviseBtn = document.querySelector('button[data-action="revise-computation"]');
        if (!reviseBtn) {
            // Fallback: find button with class "outline-action revise"
            reviseBtn = document.querySelector('button.outline-action.revise');
        }

        if (reviseBtn) {
            reviseBtn.addEventListener('click', function () {
                var n = prompt('Optional - describe what needs to be corrected:', '');
                if (n === null) return; // User cancelled

                var form = document.getElementById('form-revise');
                if (form) {
                    var notesInput = document.getElementById('notes-revise');
                    if (notesInput) {
                        notesInput.value = n;
                    }

                    if (confirm('Request revision? The declarant will be notified to review and recompute.')) {
                        form.submit();
                    }
                }
            });
        }
    }

    /* ───────────────────────────────────────────────────────────
       Approve Computation Button
       ─────────────────────────────────────────────────────────── */

    function setupApprovalButton() {
        var approveBtn = document.querySelector('button[data-action="approve-computation"]');
        if (!approveBtn) {
            // Fallback: find button with class "outline-action approve"
            approveBtn = document.querySelector('button.outline-action.approve');
        }

        if (approveBtn) {
            approveBtn.addEventListener('click', function (e) {
                if (!confirm('Approve the computation and proceed to customs lodgement?')) {
                    e.preventDefault();
                }
            });
        }
    }

})();
