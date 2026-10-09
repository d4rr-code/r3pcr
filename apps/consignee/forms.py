from django import forms

from apps.shipments.models import Shipment


class ShipmentSubmissionForm(forms.ModelForm):
    description = forms.CharField(required=True)
    shipment_type = forms.ChoiceField(
        choices=Shipment.SHIPMENT_TYPE_CHOICES,
        required=True,
    )
    invoice_currency = forms.ChoiceField(
        choices=Shipment.CURRENCY_CHOICES,
        required=True,
    )

    class Meta:
        model = Shipment
        fields = (
            'import_type',
            'invoice_currency',
            'urgency',
            'shipment_type',
            'estimated_arrival_date',
            'description',
        )


def submission_form_values(form):
    values = {}
    for name in form.fields:
        value = form[name].value()
        if hasattr(value, 'isoformat'):
            value = value.isoformat()
        values[name] = value or ''
    return values
