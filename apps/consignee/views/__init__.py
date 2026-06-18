"""apps.consignee.views package.

Split from the original ~1550-line views.py into cohesive submodules
(common / pages / submissions / exports). This __init__ re-exports the public
surface so `from . import views; views.X` in urls.py (and any other importer)
keeps working unchanged.
"""
from .common import consignee_required, generate_hawb
from .pages import (
    dashboard, system_reference, report_issue,
    system_parameters, system_fees, system_wmcda, chart_data,
)
from .submissions import (
    submit_shipment, edit_submission, my_submissions, shipment_detail, upload_receipt,
    submit_feedback, approve_computation, revise_computation,
    reject_computation, cancel_submission, resubmit_documents,
)
from .exports import download_computation
