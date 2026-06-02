from django.urls import path
from . import views

app_name = 'declarant'

urlpatterns = [
    path('dashboard/',                            views.dashboard,          name='dashboard'),
    path('system-reference/',                     views.system_reference,   name='system_reference'),
    path('system-reference/parameters/',          views.system_parameters,  name='system_parameters'),
    path('system-reference/fees/',                views.system_fees,        name='system_fees'),
    path('system-reference/wmcda/',               views.system_wmcda,       name='system_wmcda'),
    path('system-reference/hs-codes/',            views.tariff_book,        name='system_hs_codes'),
    path('tariff-book/',                          views.tariff_book,        name='tariff_book'),
    path('tariff-book/section/<int:section_num>/', views.tariff_book_section, name='tariff_book_section'),
    path('tariff-book/chapter/<int:chapter_num>/', views.tariff_book_chapter, name='tariff_book_chapter'),
    path('preview/<int:shipment_id>/',            views.shipment_preview,   name='preview'),
    path('queue/', views.queue_manager, name='queue'),
    path('claim/<int:shipment_id>/', views.claim_shipment, name='claim'),
    path('process/<int:shipment_id>/', views.process_shipment, name='process'),
    path('process/<int:shipment_id>/proceed-to-lodgement/', views.proceed_to_lodgement, name='proceed_to_lodgement'),
    path('update-status/<int:shipment_id>/', views.update_status, name='update_status'),
    path('update-shipping-mode/<int:shipment_id>/', views.update_shipping_mode, name='update_shipping_mode'),
    path('payment/<int:shipment_id>/', views.payment_confirmation, name='payment'),
    path('flag-deficiency/<int:shipment_id>/', views.flag_deficiency, name='flag_deficiency'),
    path('shipment/<int:shipment_id>/upload-sad/',     views.upload_sad,      name='upload_sad'),
    path('shipment/<int:shipment_id>/upload-receipt/', views.upload_receipt,  name='upload_receipt'),
    path('save-ocr-items/<int:shipment_id>/',     views.save_ocr_items,   name='save_ocr_items'),
    path('process/<int:shipment_id>/ocr-sync/',   views.run_ocr_sync,     name='run_ocr_sync'),
]
