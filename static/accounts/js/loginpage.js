/* =========================================
   PASSWORD TOGGLE
========================================= */

const togglePassword =
document.getElementById("togglePassword");

const password =
document.getElementById("password");

togglePassword.addEventListener("click", function(){

    const type =
    password.getAttribute("type") === "password"
    ? "text"
    : "password";

    password.setAttribute("type", type);

    this.innerHTML =
    type === "password"
    ? '<i class="fa-regular fa-eye"></i>'
    : '<i class="fa-regular fa-eye-slash"></i>';

});

/* =========================================
   CUSTOM LANGUAGE DROPDOWN
========================================= */

const languageDropdown =
document.querySelector(".language-dropdown");

const selectedLanguage =
document.getElementById("selectedLanguage");

const currentLanguage =
document.getElementById("currentLanguage");

const languageOptions =
document.querySelectorAll(".language-option");

/* =========================================
   OPEN / CLOSE DROPDOWN
========================================= */

selectedLanguage.addEventListener("click", () => {

    languageDropdown.classList.toggle("active");

});

/* =========================================
   LANGUAGE OPTION CLICK
   Translation + persistence handled by i18n.js (data-en / data-fil).
========================================= */

/* =========================================
   CLOSE DROPDOWN OUTSIDE CLICK
========================================= */

window.addEventListener("click", (e) => {

    if(!languageDropdown.contains(e.target)){

        languageDropdown.classList.remove("active");

    }

});