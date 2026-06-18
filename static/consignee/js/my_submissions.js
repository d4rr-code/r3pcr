(function () {
    function replaceSectionFrom(url, targetId) {
        fetch(url, {
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            credentials: 'same-origin'
        })
            .then(function (response) {
                if (!response.ok) {
                    throw new Error('Pagination request failed');
                }
                return response.text();
            })
            .then(function (html) {
                var doc = new DOMParser().parseFromString(html, 'text/html');
                var nextSection = doc.getElementById(targetId);
                var currentSection = document.getElementById(targetId);

                if (!nextSection || !currentSection) {
                    window.location.href = url;
                    return;
                }

                currentSection.replaceWith(nextSection);
                window.history.pushState({}, '', url);
            })
            .catch(function () {
                window.location.href = url;
            });
    }

    document.addEventListener('click', function (event) {
        var link = event.target.closest('.pagination-bar a.page-link');
        if (!link) {
            return;
        }

        var pagination = link.closest('.pagination-bar');
        var targetId = pagination ? pagination.getAttribute('data-pagination-target') : '';
        if (!targetId) {
            return;
        }

        event.preventDefault();
        replaceSectionFrom(link.href, targetId);
    });
}());
