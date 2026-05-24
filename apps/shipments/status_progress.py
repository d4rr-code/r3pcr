PROGRESS_STATUS_KEYS = [
    'incoming',
    'arrived',
    'computed',
    'approved',
    'lodgement',
    'ongoing',
    'assessed',
    'paid',
    'released',
    'billed',
]

ROLE_STATUS_LABELS = {
    'consignee': {
        'incoming':  'Submitted',
        'arrived':   'Arrived',
        'computed':  'Computed',
        'approved':  'Approved',
        'lodgement': 'Lodgement',
        'ongoing':   'Ongoing',
        'assessed':  'Assessed',
        'paid':      'Paid',
        'released':  'Released',
        'billed':    'Billed',
    },
    'declarant': {
        'incoming': 'Incoming',
        'arrived': 'Arrived',
        'computed': 'Computed',
        'approved': 'Approved',
        'lodgement': 'Lodgement',
        'ongoing': 'Ongoing',
        'assessed': 'Assessed',
        'paid': 'Paid',
        'released': 'Released',
        'billed': 'Billed',
    },
}


def build_status_progress(status, role):
    labels = ROLE_STATUS_LABELS.get(role, ROLE_STATUS_LABELS['declarant'])
    try:
        current_index = PROGRESS_STATUS_KEYS.index(status)
    except ValueError:
        current_index = -1

    steps = []
    for index, key in enumerate(PROGRESS_STATUS_KEYS):
        if current_index == -1:
            state = 'future'
        elif index < current_index:
            state = 'completed'
        elif index == current_index:
            state = 'current'
        else:
            state = 'future'
        steps.append({
            'key': key,
            'label': labels[key],
            'state': state,
        })
    return steps
