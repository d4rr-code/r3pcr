(function () {

    const STORAGE_KEY = "sidebar_expanded";

    const sidebar = document.getElementById("c-sidebar");
    const main = document.getElementById("c-main");

    if (!sidebar || !main) {
        return;
    }

    /* ===============================================================
       Restore Sidebar State
    =============================================================== */

    const savedState = localStorage.getItem(STORAGE_KEY);

    if (savedState === "1" && window.innerWidth > 1024) {

        sidebar.classList.add("expanded");
        main.classList.add("sidebar-open");

    }

    /* ===============================================================
       Desktop Toggle
    =============================================================== */

    window.toggleSidebar = function () {

        const isExpanded = sidebar.classList.toggle("expanded");

        main.classList.toggle("sidebar-open", isExpanded);

        localStorage.setItem(
            STORAGE_KEY,
            isExpanded ? "1" : "0"
        );

    };

})();

/* ===================================================================
   Mobile Sidebar
=================================================================== */

function toggleMobileSidebar() {

    const sidebar = document.getElementById("c-sidebar");
    const overlay = document.getElementById("c-overlay");
    const hamburger = document.getElementById("c-hamburger");

    if (!sidebar || !overlay) {
        return;
    }

    const isOpen = sidebar.classList.toggle("mobile-open");
    overlay.classList.toggle("visible", isOpen);
    document.body.classList.toggle("c-menu-open", isOpen);

    if (hamburger) {
        hamburger.setAttribute("aria-expanded", isOpen ? "true" : "false");
    }

}

function closeMobileSidebar() {

    const sidebar = document.getElementById("c-sidebar");
    const overlay = document.getElementById("c-overlay");
    const hamburger = document.getElementById("c-hamburger");

    if (!sidebar || !overlay) {
        return;
    }

    sidebar.classList.remove("mobile-open");
    overlay.classList.remove("visible");
    document.body.classList.remove("c-menu-open");

    if (hamburger) {
        hamburger.setAttribute("aria-expanded", "false");
    }

}

/* ===================================================================
   Close Mobile Sidebar on Resize
=================================================================== */

window.addEventListener("resize", function () {

    if (window.innerWidth > 768) {

        closeMobileSidebar();

    }

});

/* ===================================================================
   Close Mobile Sidebar when Navigation is Clicked
=================================================================== */

document.addEventListener("DOMContentLoaded", function () {

    const navItems = document.querySelectorAll(".c-nav-item");

    navItems.forEach(function (item) {

        item.addEventListener("click", function () {

            if (window.innerWidth <= 768) {

                closeMobileSidebar();

            }

        });

    });

});
