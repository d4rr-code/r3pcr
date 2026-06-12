(function () {
    'use strict';

    var STATUS_COLORS = {
        'incoming':     { bg: '#F1F5F9', text: '#475569' },
        'arrived':      { bg: '#FFF7ED', text: '#C2410C' },
        'computed':     { bg: '#F5F3FF', text: '#6D28D9' },
        'approved':     { bg: '#F0FDF4', text: '#15803D' },
        'lodgement':    { bg: '#EFF6FF', text: '#1D4ED8' },
        'ongoing':      { bg: '#EFF6FF', text: '#1D4ED8' },
        'assessed':     { bg: '#FFFBEB', text: '#B45309' },
        'paid':         { bg: '#F0FDF4', text: '#15803D' },
        'released':     { bg: '#ECFDF5', text: '#047857' },
        'billed':       { bg: '#FFF7ED', text: '#C2410C' },
        'for_revision': { bg: '#FEFCE8', text: '#A16207' },
        'rejected':     { bg: '#FEF2F2', text: '#B91C1C' },
    };

    var TYPE_COLORS = {
        'submission':   { bg: '#EFF6FF', text: '#1D4ED8' },
        'status_update':{ bg: '#F5F3FF', text: '#6D28D9' },
        'computation':  { bg: '#F5F3FF', text: '#6D28D9' },
        'advisory':     { bg: '#FFFBEB', text: '#B45309' },
        'payment':      { bg: '#FFF7ED', text: '#C2410C' },
        'approved':     { bg: '#F0FDF4', text: '#15803D' },
        'rejected':     { bg: '#FEF2F2', text: '#B91C1C' },
        'arrived':      { bg: '#FFF7ED', text: '#C2410C' },
        'computed':     { bg: '#F5F3FF', text: '#6D28D9' },
        'for_revision': { bg: '#FEFCE8', text: '#A16207' },
        'billed':       { bg: '#FFF7ED', text: '#C2410C' },
        'announcement':  { bg: '#EFF6FF', text: '#1D4ED8' },
        'general':      { bg: '#F3F4F6', text: '#374151' },
    };

    /* URL templates must be provided by the template as globals:
       window.NOTIF_URL_JSON (e.g. '{% url "notifications:json" 0 %}')
       window.SHIPMENT_URL_TPL (e.g. '{% url "consignee:shipment_detail" 99999 %}')
    */

    function timeAgo(isoStr) {
        var now  = new Date();
        var then = new Date(isoStr);
        var secs = Math.floor((now - then) / 1000);
        if (secs < 60)  return 'just now';
        var mins = Math.floor(secs / 60);
        if (mins < 60)  return mins + ' min' + (mins > 1 ? 's' : '') + ' ago';
        var hrs  = Math.floor(mins / 60);
        if (hrs  < 24)  return hrs  + ' hr'  + (hrs  > 1 ? 's' : '') + ' ago';
        var days = Math.floor(hrs  / 24);
        if (days < 30)  return days + ' day' + (days > 1 ? 's' : '') + ' ago';
        var mos  = Math.floor(days / 30);
        return mos + ' month' + (mos > 1 ? 's' : '') + ' ago';
    }

    function stampRelativeTimes() {
        document.querySelectorAll('.notif-row').forEach(function (row) {
            var ts   = row.getAttribute('data-ts');
            var date = row.getAttribute('data-date');
            var el   = row.querySelector('.ts-label');
            if (el && ts) { el.textContent = timeAgo(ts) + ' · ' + date; }
        });
    }

    function setText(id, val) {
        var el = document.getElementById(id);
        if (el) el.textContent = val || '—';
    }
    function setTextOrHide(id, val) {
        var el = document.getElementById(id);
        if (!el) return;
        if (val) { el.textContent = val; el.style.display = ''; }
        else     { el.textContent = ''; el.style.display = 'none'; }
    }

    function openNotifModal(id) {
        var backdrop = document.getElementById('notif-backdrop');
        var loading  = document.getElementById('modal-loading');
        var content  = document.getElementById('modal-content');
        backdrop.classList.add('open');
        loading.style.display  = 'block';
        content.style.display  = 'none';

        var row = document.querySelector('.notif-row[data-id="' + id + '"]');
        if (row) {
            row.classList.remove('unread');
            var dot   = row.querySelector('.notif-dot');
            var title = row.querySelector('.notif-title');
            if (dot)   { dot.classList.remove('unread'); dot.classList.add('read'); }
            if (title) { title.classList.remove('unread'); title.classList.add('read'); }
        }

        var urlTpl = window.NOTIF_URL_JSON || '';
        if (!urlTpl) {
            loading.innerHTML = '<span class="ajax-error">Failed to load. Please try again.</span>';
            return;
        }
        var url = urlTpl.replace('/0/', '/' + id + '/');

        fetch(url)
            .then(function (r) {
                if (!r.ok) throw new Error('Network error');
                return r.json();
            })
            .then(function (d) {
                var isAnnouncement = !!d.is_announcement;
                var infoGrid = document.getElementById('modal-info-grid');
                var divider  = document.getElementById('modal-divider');
                var footer   = document.getElementById('modal-footer');

                setText('modal-hawb', isAnnouncement ? d.announcement_title : (d.hawb_number || d.title || 'General Notification'));
                setText('modal-created', d.created_at);

                var badge  = document.getElementById('modal-badge');
                var colors = STATUS_COLORS[d.status_code]
                          || TYPE_COLORS[d.notification_type]
                          || { bg: '#F3F4F6', text: '#374151' };
                badge.textContent       = isAnnouncement ? (d.announcement_category || 'Announcement') : (d.status_display || d.notification_type || '-');
                badge.style.background  = colors.bg;
                badge.style.color       = colors.text;

                setText('modal-message', isAnnouncement ? d.announcement_content : d.message);

                if (infoGrid) infoGrid.style.display = isAnnouncement ? 'none' : 'grid';
                if (divider) divider.style.display = isAnnouncement ? 'none' : '';
                if (footer) footer.style.display = isAnnouncement ? 'none' : '';

                if (!isAnnouncement) {
                    setText('modal-status-display',  d.status_display);
                    setTextOrHide('modal-status-sublabel', d.status_sublabel);
                    setTextOrHide('modal-declarant',  d.declarant ? 'Handled by ' + d.declarant : '');
                    setText('modal-next-step',  d.next_step);
                    setText('modal-submitted',  d.submitted_at);
                    setText('modal-updated',    d.updated_at);
                }

                var btn = document.getElementById('modal-view-btn');
                if (d.shipment_id && window.SHIPMENT_URL_TPL) {
                    btn.href = window.SHIPMENT_URL_TPL.replace('99999', d.shipment_id);
                    btn.style.display = 'flex';
                } else if (btn) {
                    btn.style.display = 'none';
                }

                loading.style.display = 'none';
                content.style.display = 'block';
            })
            .catch(function () {
                loading.innerHTML = '<span class="ajax-error">Failed to load. Please try again.</span>';
            });
    }

    function closeNotifModal() {
        var backdrop = document.getElementById('notif-backdrop');
        if (backdrop) backdrop.classList.remove('open');
    }

    document.addEventListener('DOMContentLoaded', function () {
        stampRelativeTimes();

        document.querySelectorAll('.notif-row').forEach(function (row) {
            row.addEventListener('click', function () {
                var id = row.getAttribute('data-id');
                if (id) openNotifModal(id);
            });
        });

        var backdrop = document.getElementById('notif-backdrop');
        var modal    = document.getElementById('notif-modal');
        var closeBtn = document.querySelector('.modal-close');

        if (backdrop) backdrop.addEventListener('click', closeNotifModal);
        if (modal) modal.addEventListener('click', function (e) { e.stopPropagation(); });
        if (closeBtn) closeBtn.addEventListener('click', closeNotifModal);

        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape') closeNotifModal();
        });

        // expose on window for inline onclick handlers used in template
        window.openNotifModal = openNotifModal;
        window.closeNotifModal = closeNotifModal;
    });

})();
