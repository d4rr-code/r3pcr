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

/* OPTION CLICK — translation + persistence handled by i18n.js */

/* CLOSE OUTSIDE */

window.addEventListener("click", () => {

    languageDropdown.classList.remove("active");

});

/* =========================================
   HAMBURGER MENU
========================================= */

const navHamburger = document.getElementById("navHamburger");
const navRight = document.getElementById("navRight");

navHamburger.addEventListener("click", (e) => {

    e.stopPropagation();

    navHamburger.classList.toggle("open");
    navRight.classList.toggle("open");

});

window.addEventListener("click", (e) => {

    if(!navRight.contains(e.target) && !navHamburger.contains(e.target)){
        navHamburger.classList.remove("open");
        navRight.classList.remove("open");
    }

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