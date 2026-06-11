
const labels = document.querySelectorAll(".star-label");
const ratingLabel = document.getElementById(
    "rating-label"
);
const messages = [
    "",
    "Poor",
    "Fair",
    "Good",
    "Very Good",
    "Excellent"
];

/* ==========================================================================
   STAR EVENTS
   ========================================================================== */

labels.forEach(function (label, index) {

    label.addEventListener(

        "click",

        function () {

            const value = index + 1;

            const radio = label.querySelector("input");

            if (radio) {

                radio.checked = true;

            }

            labels.forEach(function (star, i) {

                star.style.color =

                    i < value

                        ? "#F59E0B"

                        : "#D1D5DB";

            });

            if (ratingLabel) {

                ratingLabel.textContent =

                    messages[value] +

                    " (" +

                    value +

                    "/5)";

                ratingLabel.style.color =

                    "#F59E0B";

            }

        }

    );

    label.addEventListener(

        "mouseover",

        function () {

            labels.forEach(function (star, i) {

                star.style.color =

                    i <= index

                        ? "#FBBF24"

                        : "#D1D5DB";

            });

        }

    );

    label.addEventListener(

        "mouseout",

        function () {

            const checked = document.querySelector(

                'input[name="rating"]:checked'

            );

            const value = checked

                ? parseInt(

                    checked.value,

                    10

                )

                : 0;

            labels.forEach(function (

                star,

                i

            ) {

                star.style.color =

                    i < value

                        ? "#F59E0B"

                        : "#D1D5DB";

            });

        }

    );

});

window.addEventListener(

    "DOMContentLoaded",

    function () {

        const checked = document.querySelector(

            'input[name="rating"]:checked'

        );

        if (!checked) {

            return;

        }

        const value = parseInt(

            checked.value,

            10

        );

        labels.forEach(function (

            star,

            index

        ) {

            star.style.color =

                index < value

                    ? "#F59E0B"

                    : "#D1D5DB";

        });

        if (ratingLabel) {

            ratingLabel.textContent =

                messages[value] +

                " (" +

                value +

                "/5)";

            ratingLabel.style.color =

                "#F59E0B";

        }

    }

);
