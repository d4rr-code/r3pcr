/* =========================================================================
   R3-PCR lightweight client-side i18n for PUBLIC pages (EN / Filipino).

   Usage in templates:
     - Text:        <h2 data-en="Welcome" data-fil="Maligayang pagdating">Welcome</h2>
     - Placeholder: <input data-en-ph="Search" data-fil-ph="Maghanap" placeholder="Search">

   The chosen language is saved in localStorage so it persists across the
   public pages (landing, login, register, verify, forgot). The authenticated
   app is unaffected.
   ========================================================================= */
(function () {
    var KEY = 'r3pcrLang';

    function getLang() {
        var l = localStorage.getItem(KEY);
        return l === 'fil' ? 'fil' : 'en';
    }
    function setLang(l) {
        localStorage.setItem(KEY, l === 'fil' ? 'fil' : 'en');
    }

    function apply(lang) {
        // Text content
        document.querySelectorAll('[data-en]').forEach(function (el) {
            var t = el.getAttribute('data-' + lang);
            if (t !== null) el.textContent = t;
        });
        // Placeholders
        document.querySelectorAll('[data-en-ph]').forEach(function (el) {
            var t = el.getAttribute('data-' + lang + '-ph');
            if (t !== null) el.setAttribute('placeholder', t);
        });
        // Toggle label + document language
        var cur = document.getElementById('currentLanguage');
        if (cur) cur.textContent = lang === 'fil' ? 'FIL' : 'EN';
        document.documentElement.setAttribute('lang', lang === 'fil' ? 'fil' : 'en');
        // Mark active option
        document.querySelectorAll('.language-option[data-lang]').forEach(function (opt) {
            opt.classList.toggle('active', opt.getAttribute('data-lang') === lang);
        });
    }

    function wire() {
        document.querySelectorAll('.language-option[data-lang]').forEach(function (opt) {
            opt.addEventListener('click', function () {
                var l = opt.getAttribute('data-lang');
                setLang(l);
                apply(l);
                var dd = document.querySelector('.language-dropdown');
                if (dd) dd.classList.remove('active');
            });
        });
        apply(getLang());
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', wire);
    } else {
        wire();
    }
})();
