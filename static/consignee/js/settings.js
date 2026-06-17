
(function () {
    'use strict';

    /* ───────────────────────────────────────────────────────────
       DOM Elements
       ─────────────────────────────────────────────────────────── */

    var viewMode = document.getElementById('view-mode');
    var editMode = document.getElementById('edit-mode');
    var editBtn = null;
    var cancelBtns = [];
    var newPwInput = document.getElementById('new-pw');
    var confirmPwInput = document.getElementById('confirm-pw');
    var newPwForm = null;

    /* ───────────────────────────────────────────────────────────
       View Mode Toggle
       ─────────────────────────────────────────────────────────── */

    function showEdit() {
        if (viewMode) viewMode.style.display = 'none';
        if (editMode) editMode.style.display = 'block';
    }

    function showView() {
        if (editMode) editMode.style.display = 'none';
        if (viewMode) viewMode.style.display = 'block';
    }

    /* ───────────────────────────────────────────────────────────
       Password Validation
       ─────────────────────────────────────────────────────────── */

    function checkPw(val) {
        setReq('req-len', val.length >= 8);
        setReq('req-upper', /[A-Z]/.test(val));
        setReq('req-num', /[0-9]/.test(val));
        checkMatch();
    }

    function checkMatch() {
        var pw = newPwInput ? newPwInput.value : '';
        var cfm = confirmPwInput ? confirmPwInput.value : '';
        setReq('req-match', pw.length > 0 && pw === cfm);
    }

    function setReq(id, ok) {
        var el = document.getElementById(id);
        var icon = document.getElementById(id + '-icon');
        if (!el || !icon) return;

        if (ok) {
            el.classList.add('met');
            icon.textContent = '✓';
        } else {
            el.classList.remove('met');
            icon.textContent = '○';
        }
    }

    /* ───────────────────────────────────────────────────────────
       DOM Ready Initialization
       ─────────────────────────────────────────────────────────── */

    document.addEventListener('DOMContentLoaded', function () {
        // Find edit button
        editBtn = viewMode ? viewMode.querySelector('.settings-edit-btn, button[onclick*="showEdit"]') : null;

        // Find all cancel buttons
        var allBtns = document.querySelectorAll('button');
        allBtns.forEach(function (btn) {
            if (btn.textContent.toLowerCase() === 'cancel') {
                cancelBtns.push(btn);
                btn.addEventListener('click', showView);
            }
        });

        // Find edit button and add listener
        if (editBtn) {
            editBtn.addEventListener('click', showEdit);
        }

        // Setup password input listeners
        if (newPwInput) {
            newPwInput.addEventListener('input', function () {
                checkPw(this.value);
            });
        }

        if (confirmPwInput) {
            confirmPwInput.addEventListener('input', checkMatch);
        }

        // Check if we should show edit mode (if there were validation errors)
        var errorState = document.querySelector('[data-form-errors]');
        var hasErrors = errorState && errorState.getAttribute('data-form-errors') === 'true';
        if (hasErrors) {
            showEdit();
        }
    });

    /* ───────────────────────────────────────────────────────────
       Expose functions to window for backward compatibility
       ─────────────────────────────────────────────────────────── */

    window.showEdit = showEdit;
    window.showView = showView;
    window.checkPw = checkPw;
    window.checkMatch = checkMatch;
    window.setReq = setReq;

})();
