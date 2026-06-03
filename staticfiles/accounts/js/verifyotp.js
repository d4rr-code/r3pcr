/* =========================================
   LANGUAGE DROPDOWN
========================================= */

const languageDropdown =
document.querySelector(".language-dropdown");

const selectedLanguage =
document.getElementById("selectedLanguage");

const currentLanguage =
document.getElementById("currentLanguage");

const languageOptions =
document.querySelectorAll(".language-option");

/* OPEN/CLOSE */

selectedLanguage.addEventListener("click", (e) => {

    e.stopPropagation();

    languageDropdown.classList.toggle("active");

});

/* OPTION CLICK */

languageOptions.forEach(option => {

    option.addEventListener("click", () => {

        const lang =
        option.getAttribute("data-lang");

        currentLanguage.innerText =
        lang === "en" ? "EN" : "FIL";

        languageDropdown.classList.remove("active");

        /* FILIPINO */

        if(lang === "fil"){

            document.getElementById("otpTitle")
            .innerText = "Suriin ang iyong Email";

            document.getElementById("otpSubtitle")
            .innerText =
            "Nagpadala kami ng 6-digit OTP sa iyong email. Mag-e-expire ito sa loob ng 10 minuto.";

            document.getElementById("verifyBtn")
            .innerText = "BERIPIKAHIN";

            document.getElementById("backText")
            .innerText = "← Bumalik sa Login";

            document.getElementById("followText")
            .innerText = "I-FOLLOW KAMI";

            document.getElementById("contactText")
            .innerText = "KONTAKIN KAMI";

        }

        /* ENGLISH */

        else{

            document.getElementById("otpTitle")
            .innerText = "Check your Email";

            document.getElementById("otpSubtitle")
            .innerText =
            "We sent a 6-digit OTP to your email address. It expires in 10 minutes.";

            document.getElementById("verifyBtn")
            .innerText = "VERIFY OTP";

            document.getElementById("backText")
            .innerText = "← Back to Login";

            document.getElementById("followText")
            .innerText = "FOLLOW US";

            document.getElementById("contactText")
            .innerText = "CONTACT US";

        }

    });

});

/* CLOSE OUTSIDE */

window.addEventListener("click", () => {

    languageDropdown.classList.remove("active");

});