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
        'incoming':  'Incoming',
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
}

# Descriptive sub-labels shown to the consignee below the progress bar
CONSIGNEE_STATUS_SUBLABELS = {
    'incoming':  'Your shipment has been submitted. A declarant will review it shortly.',
    'arrived':   'Awaits Revised Invoice — The declarant is reviewing your documents.',
    'computed':  'Awaits CDT Approval — Your Estimated Duties & Tax computation is ready. Please review and approve below.',
    'approved':  'Awaits Manifest Validation — Your approval has been received. The declarant is now proceeding.',
    'lodgement': 'The declarant is filing your entry with the Bureau of Customs (BOC).',
    'ongoing':   'Lined Up for Final Assessment — Your shipment is in queue for BOC assessment.',
    'assessed':  'Awaits Payment of D/T — BOC has assessed your shipment. Refer to the FAN document below for the official amount due.',
    'paid':      'Awaits CNTR Discharge & Delivery Schedule — Payment confirmed. Your goods are being processed for release.',
    'released':  'Your shipment has been released from customs. Delivery is being arranged.',
    'billed':    'Your shipment is complete and billed. Thank you for trusting RTripleJ Customs Brokerage!',
}


def build_status_progress(status, role):
    labels    = ROLE_STATUS_LABELS.get(role, ROLE_STATUS_LABELS['declarant'])
    sublabels = CONSIGNEE_STATUS_SUBLABELS if role == 'consignee' else {}
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
            'key':      key,
            'label':    labels[key],
            'sublabel': sublabels.get(key, ''),
            'state':    state,
        })
    return steps
