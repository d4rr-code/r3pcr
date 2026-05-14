/* ── Live username preview ─────────────────────────────────── */
function generateUsername(first, last) {
    return (first + last).toLowerCase().replace(/[^a-z0-9]/g, '') || '';
}

const firstInput   = document.getElementById('first_name');
const lastInput    = document.getElementById('last_name');
const preview      = document.getElementById('username-preview');
const previewValue = document.getElementById('username-preview-value');

function updatePreview() {
    const base = generateUsername(firstInput.value.trim(), lastInput.value.trim());
    if (base) {
        previewValue.textContent = base;
        preview.classList.add('visible');
    } else {
        preview.classList.remove('visible');
    }
}

firstInput.addEventListener('input', updatePreview);
lastInput.addEventListener('input',  updatePreview);

/* ── Password toggle ───────────────────────────────────────── */
document.querySelectorAll('.toggle-pw').forEach(btn => {
    btn.addEventListener('click', function () {
        const input = this.previousElementSibling;
        const show  = input.type === 'password';
        input.type  = show ? 'text' : 'password';
        this.innerHTML = show
            ? '<i class="fa-regular fa-eye-slash"></i>'
            : '<i class="fa-regular fa-eye"></i>';
    });
});

/* ── Password strength hints ───────────────────────────────── */
const pwInput = document.getElementById('password');
const hints   = {
    len:   document.getElementById('hint-len'),
    upper: document.getElementById('hint-upper'),
    lower: document.getElementById('hint-lower'),
    num:   document.getElementById('hint-num'),
    spec:  document.getElementById('hint-spec'),
};

function setHint(el, ok) {
    el.className = 'hint ' + (ok ? 'ok' : 'fail');
    el.querySelector('.hint-icon').textContent = ok ? '✓' : '✗';
}

pwInput && pwInput.addEventListener('input', function () {
    const v = this.value;
    setHint(hints.len,   v.length >= 8);
    setHint(hints.upper, /[A-Z]/.test(v));
    setHint(hints.lower, /[a-z]/.test(v));
    setHint(hints.num,   /[0-9]/.test(v));
    setHint(hints.spec,  /[^A-Za-z0-9]/.test(v));
});

/* ── Language dropdown ─────────────────────────────────────── */
const languageDropdown = document.querySelector('.language-dropdown');
if (languageDropdown) {
    document.getElementById('selectedLanguage').addEventListener('click', () => {
        languageDropdown.classList.toggle('active');
    });
    window.addEventListener('click', e => {
        if (!languageDropdown.contains(e.target))
            languageDropdown.classList.remove('active');
    });
}
