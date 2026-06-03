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
========================================= */

languageOptions.forEach(option => {

    option.addEventListener("click", () => {

        const lang =
        option.getAttribute("data-lang");

        /* =================================
           UPDATE NAV LABEL
        ================================= */

        if(lang === "en"){

            currentLanguage.innerText = "EN";

        }

        else{

            currentLanguage.innerText = "FIL";

        }

        /* CLOSE MENU */

        languageDropdown.classList.remove("active");

        /* =================================
           FILIPINO TRANSLATION
        ================================= */

        if(lang === "fil"){

            document.getElementById("welcomeText")
            .innerText = "Maligayang pagbabalik!";

            document.getElementById("subtitleText")
            .innerText =
            "Isang hakbang ka na lamang tungo sa mas mahusay na shipment visibility.";

            document.getElementById("usernameLabel")
            .innerText = "USERNAME";

            document.getElementById("passwordLabel")
            .innerText = "PASSWORD";

            document.getElementById("rememberText")
            .innerText = "Tandaan ako";

            document.getElementById("loginBtn")
            .innerText = "MAG LOG IN";

            document.getElementById("helpText")
            .innerHTML =
            'Kailangan ng tulong sa iyong <a href="#">username</a> o <a href="#">password</a>?';

            document.getElementById("registerText")
            .innerHTML =
            'Wala ka pang account? <a href="#">Mag Register</a>';

            document.getElementById("followText")
            .innerText = "I-FOLLOW KAMI";

            document.getElementById("contactText")
            .innerText = "KONTAKIN KAMI";

        }

        /* =================================
           ENGLISH TRANSLATION
        ================================= */

        else{

            document.getElementById("welcomeText")
            .innerText = "Welcome Back!";

            document.getElementById("subtitleText")
            .innerText =
            "You're one step away from better shipment visibility.";

            document.getElementById("usernameLabel")
            .innerText = "USERNAME";

            document.getElementById("passwordLabel")
            .innerText = "PASSWORD";

            document.getElementById("rememberText")
            .innerText = "Remember Me";

            document.getElementById("loginBtn")
            .innerText = "LOG IN";

            document.getElementById("helpText")
            .innerHTML =
            'Need help with your <a href="#">username</a> or <a href="#">password</a>?';

            document.getElementById("registerText")
            .innerHTML =
            'Don’t have account yet? <a href="#">Register Now</a>';

            document.getElementById("followText")
            .innerText = "FOLLOW US";

            document.getElementById("contactText")
            .innerText = "CONTACT US";

        }

    });

});

/* =========================================
   CLOSE DROPDOWN OUTSIDE CLICK
========================================= */

window.addEventListener("click", (e) => {

    if(!languageDropdown.contains(e.target)){

        languageDropdown.classList.remove("active");

    }

});