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

            document.getElementById("heroTitle")
            .innerText =
            "Pasimplehin ang iyong pre-clearance process";

            document.getElementById("heroSubtitle")
            .innerText =
            "Pabilisin ang iyong customs pre-clearance workflow gamit ang automated document handling at real-time shipment monitoring.";

        }

        /* ENGLISH */

        else{

            document.getElementById("heroTitle")
            .innerText =
            "Simplify your pre-clearance process";

            document.getElementById("heroSubtitle")
            .innerText =
            "Streamline your customs pre-clearance workflow with automated document handling and real-time shipment monitoring.";

        }

    });

});

/* CLOSE OUTSIDE */

window.addEventListener("click", () => {

    languageDropdown.classList.remove("active");

});

/* =========================================
   TRACKING TABS
========================================= */

const trackingTab =
document.getElementById("trackingTab");

const locationTab =
document.getElementById("locationTab");

const trackingContent =
document.getElementById("trackingContent");

const locationContent =
document.getElementById("locationContent");

/* TRACKING */

trackingTab.addEventListener("click", () => {

    trackingTab.classList.add("active");
    locationTab.classList.remove("active");

    trackingContent.classList.add("active");
    locationContent.classList.remove("active");

});

/* LOCATION */

locationTab.addEventListener("click", () => {

    locationTab.classList.add("active");
    trackingTab.classList.remove("active");

    locationContent.classList.add("active");
    trackingContent.classList.remove("active");

});

/* =========================================
   SHOWCASE CAROUSEL
========================================= */

/* =========================================
   SHOWCASE CAROUSEL
========================================= */

document.addEventListener(
"DOMContentLoaded",

function(){

    const showcaseCards =
    document.querySelectorAll(".showcase-card");

    /* =====================================
       ACTIVATE SLIDE
    ====================================== */

    window.activateSlide = function(index){

        /* REMOVE OLD CLASSES */

        showcaseCards.forEach(card => {

            card.classList.remove(
                "left",
                "active",
                "right"
            );

        });

        /* =================================
           LEFT CLICKED
        ================================= */

        if(index === 0){

            showcaseCards[0]
            .classList.add("active");

            showcaseCards[1]
            .classList.add("right");

            showcaseCards[2]
            .classList.add("left");

        }

        /* =================================
           CENTER CLICKED
        ================================= */

        else if(index === 1){

            showcaseCards[0]
            .classList.add("left");

            showcaseCards[1]
            .classList.add("active");

            showcaseCards[2]
            .classList.add("right");

        }

        /* =================================
           RIGHT CLICKED
        ================================= */

        else{

            showcaseCards[0]
            .classList.add("right");

            showcaseCards[1]
            .classList.add("left");

            showcaseCards[2]
            .classList.add("active");

        }

    };

});