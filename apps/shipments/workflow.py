"""Shared rules for estimate editing and operational milestone evidence."""
TRANSITIONS = {
    'approved': ('lodgement',),
    'lodgement': ('ongoing',),
    'ongoing': ('assessed',),
    'assessed': ('paid',),
    'paid': ('released',),
    'released': ('billed',),
}
DRAFT_STATUSES = {'arrived', 'for_revision', 'rejected'}
COMPUTABLE_STATUSES = DRAFT_STATUSES | {'computed', 'approved'}
REQUIRED_EVIDENCE = {
    'assessed': {'sad'},
    'paid': {'sad', 'payment_proof'},
    'released': {'sad', 'payment_proof', 'release_doc'},
    'billed': {'sad', 'payment_proof', 'release_doc', 'billing_doc'},
}


def transition_error(shipment, new_status, extra_documents=()):
    if new_status not in TRANSITIONS.get(shipment.status, ()):
        return 'Only the next operational milestone is permitted.'
    if shipment.has_deficiency:
        return 'Resolve the document deficiency before advancing this shipment.'
    if new_status == 'lodgement':
        computation = getattr(shipment, 'computation', None)
        if not computation or not computation.items_json or computation.total_landed_cost is None or computation.requires_csf_review:
            return 'A completed, client-approved computation is required before lodgement.'
    documents = set(shipment.documents.exclude(file='').values_list('document_type', flat=True)) | set(extra_documents)
    missing = REQUIRED_EVIDENCE.get(new_status, set()) - documents
    if missing:
        from .models import ShipmentDocument
        labels = dict(ShipmentDocument.DOCUMENT_TYPE_CHOICES)
        return 'Upload the required documents first: ' + ', '.join(labels[key] for key in sorted(missing)) + '.'
    return ''
