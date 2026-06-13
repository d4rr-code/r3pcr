import json
import logging
import os
import re
import tempfile
import threading
from decimal import Decimal, InvalidOperation

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.db import transaction
from django.db.models import Q, Count
from django.utils import timezone
from apps.shipments.models import Shipment, ShipmentDocument, HSCode, ShipmentHSCode, StatusLog
from apps.supervisor.models import SystemConfig
from apps.notifications.utils import notify_shipment_status_change
from ..models import DutyComputation, ShipmentLineItem, ShippingAdvisory
from ..ocr import process_document
from apps.declarant.views import declarant_required

logger = logging.getLogger('r3pcr.computation')

_RATE_KEYS = {
    'USD': 'rate_USD', 'EUR': 'rate_EUR', 'JPY': 'rate_JPY',
    'HKD': 'rate_HKD', 'CNY': 'rate_CNY', 'GBP': 'rate_GBP',
    'SGD': 'rate_SGD',
}
_RATE_DEFAULTS = {
    'USD': '59.1480', 'EUR': '65.0000', 'JPY': '0.3900',
    'HKD': '7.5700',  'CNY': '8.1500',  'GBP': '74.5000', 'SGD': '43.8000',
}

# ── Country → approximate distance to Manila (km) ─────────────────────────────
# Based on typical sea/air routing from main port/airport to Manila.
# Used to auto-populate the ECDT distance field from OCR-extracted country_of_origin.
_COUNTRY_DISTANCE_KM = {
    # ── East Asia ──────────────────────────────────────────────────────────────
    'china':                    2100, 'prc': 2100, "people's republic of china": 2100,
    'hong kong':                1150, 'hk': 1150,
    'taiwan':                   1200, 'republic of china': 1200,
    'japan':                    3100,
    'south korea':              2650, 'korea':  2650, 'republic of korea': 2650,
    'north korea':              3200, "democratic people's republic of korea": 3200,
    'mongolia':                 3800,
    # ── Southeast Asia ────────────────────────────────────────────────────────
    'vietnam':                  1600, 'viet nam': 1600,
    'thailand':                 2200,
    'malaysia':                 2300,
    'singapore':                2400,
    'indonesia':                2100,
    'cambodia':                 1800,
    'myanmar':                  2800, 'burma': 2800,
    'laos':                     2000, "lao people's democratic republic": 2000,
    'brunei':                   1900, 'brunei darussalam': 1900,
    'timor-leste':              2000, 'east timor': 2000,
    # ── South Asia ────────────────────────────────────────────────────────────
    'india':                    4200,
    'bangladesh':               3800,
    'sri lanka':                3700,
    'pakistan':                 5400,
    'nepal':                    4500,
    'maldives':                 3900,
    # ── Middle East ───────────────────────────────────────────────────────────
    'united arab emirates':     6800, 'uae': 6800,
    'saudi arabia':             7200,
    'qatar':                    7000,
    'kuwait':                   7300,
    'bahrain':                  7100,
    'oman':                     6600,
    'jordan':                   8100,
    'turkey':                   8900,
    'iran':                     6700,
    # ── Africa ────────────────────────────────────────────────────────────────
    'south africa':             10800,
    'egypt':                    8500,
    'kenya':                    8300,
    'nigeria':                  12400,
    'ethiopia':                 7800,
    # ── Europe ────────────────────────────────────────────────────────────────
    'germany':                  10400,
    'netherlands':              10600,
    'belgium':                  10700,
    'france':                   10900,
    'united kingdom':           11200, 'uk': 11200, 'great britain': 11200, 'england': 11200,
    'italy':                    9700,
    'spain':                    11500,
    'portugal':                 12100,
    'switzerland':              10400,
    'austria':                  10300,
    'sweden':                   11000,
    'norway':                   11200,
    'denmark':                  10900,
    'finland':                  11100,
    'poland':                   10300,
    'czech republic':           10300, 'czechia': 10300,
    'hungary':                  10000,
    'romania':                  9600,
    'greece':                   9400,
    'russia':                   6800,
    'ukraine':                  9300,
    # ── Americas ──────────────────────────────────────────────────────────────
    'united states':            11800, 'usa': 11800, 'us': 11800, 'united states of america': 11800,
    'canada':                   10400,
    'mexico':                   12000,
    'brazil':                   17500,
    'argentina':                18500,
    'chile':                    17200,
    'colombia':                 15800,
    'peru':                     15500,
    'venezuela':                15900,
    # ── Oceania ───────────────────────────────────────────────────────────────
    'australia':                6400,
    'new zealand':              8500,
    'papua new guinea':         2900,
}

def _lookup_distance_from_country(country_raw: str) -> int | None:
    """Return approximate km from the given country to Manila, or None if not found."""
    if not country_raw:
        return None
    key = country_raw.strip().lower()
    # Direct match
    if key in _COUNTRY_DISTANCE_KM:
        return _COUNTRY_DISTANCE_KM[key]
    # Partial match — first key that starts with the input or vice versa
    for k, v in _COUNTRY_DISTANCE_KM.items():
        if k.startswith(key) or key.startswith(k):
            return v
    return None

_DISTANCE_ALIAS_KEYS = {
    'prc', "people's republic of china", 'hk', 'republic of china', 'korea',
    'republic of korea', "democratic people's republic of korea", 'viet nam',
    'burma', "lao people's democratic republic", 'brunei darussalam',
    'east timor', 'uae', 'uk', 'great britain', 'england', 'czechia',
    'usa', 'us', 'united states of america',
}

def _country_distance_options():
    """Canonical country list for the declarant origin-country selector."""
    return [
        {'name': name.title(), 'distance': distance}
        for name, distance in sorted(_COUNTRY_DISTANCE_KM.items())
        if name not in _DISTANCE_ALIAS_KEYS
    ]



# ─── Lookup Tables ────────────────────────────────────────────────────────────

_BF_DEFAULT_TIERS = [
    {'max': 10000,    'fee': '1300'},
    {'max': 20000,    'fee': '2000'},
    {'max': 30000,    'fee': '2700'},
    {'max': 40000,    'fee': '3300'},
    {'max': 50000,    'fee': '3600'},
    {'max': 60000,    'fee': '4000'},
    {'max': 100000,   'fee': '4700'},
    {'max': 200000,   'fee': '5300', 'excess_rate': '0.00125'},
]

_IPF_DEFAULT_TIERS = [
    {'max': 25000,    'fee': '250'},
    {'max': 50000,    'fee': '500'},
    {'max': 250000,   'fee': '750'},
    {'max': 500000,   'fee': '1000'},
    {'max': 750000,   'fee': '1500'},
    {'max': 99999999, 'fee': '2000'},
]


def get_brokerage_fee(taxable_value):
    tv = float(taxable_value)
    try:
        raw = SystemConfig.get('bf_tiers', '')
        tiers = json.loads(raw) if raw else _BF_DEFAULT_TIERS
    except Exception:
        tiers = _BF_DEFAULT_TIERS
    for tier in tiers:
        if tv <= float(tier['max']):
            return Decimal(str(tier['fee']))
    last  = tiers[-1]
    excess = Decimal(str(round(tv - float(last['max']), 2)))
    rate   = Decimal(str(last.get('excess_rate', '0.00125')))
    return Decimal(str(last['fee'])) + round(excess * rate, 2)


def get_ipf(taxable_value):
    tv = float(taxable_value)
    try:
        raw = SystemConfig.get('ipf_tiers', '')
        tiers = json.loads(raw) if raw else _IPF_DEFAULT_TIERS
    except Exception:
        tiers = _IPF_DEFAULT_TIERS
    for tier in tiers:
        if tv <= float(tier['max']):
            return Decimal(str(tier['fee']))
    return Decimal(str(tiers[-1]['fee']))


def _load_currency_rates():
    """Load PHP conversion rates for all supported invoice currencies."""
    from apps.supervisor.exchange_rates import ensure_daily_exchange_rates

    ensure_daily_exchange_rates()
    rates = {}
    for code, key in _RATE_KEYS.items():
        try:
            rates[code] = str(SystemConfig.objects.get(key=key).value)
        except SystemConfig.DoesNotExist:
            rates[code] = _RATE_DEFAULTS[code]
    return rates


def normalize_charge_mode(value, shipment_type=''):
    value = (value or shipment_type or '').strip().lower()
    if value in {'fcl', 'lcl', 'air'}:
        return value
    if value == 'sea':
        return 'lcl'
    return 'air' if shipment_type == 'air' else 'lcl'


def apply_transport_charges(charge_mode, arrastre, wharfage, gross_weight=0, volume_cbm=0):
    arrastre     = Decimal(str(arrastre     or 0))
    wharfage     = Decimal(str(wharfage     or 0))
    gross_weight = Decimal(str(gross_weight or 0))
    volume_cbm   = Decimal(str(volume_cbm   or 0))
    revenue_ton  = max(volume_cbm, gross_weight / Decimal('1000'))

    # Arrastre and wharfage are FLAT total amounts entered by the declarant.
    # Verified from RTripleJ CDT Excel: the declarant enters the actual
    # terminal charge for the shipment — NOT a per-ton rate to be multiplied.
    # (The ₱5,496 and ₱519.35 references are starting hints, not multipliers.)
    return arrastre, wharfage, revenue_ton


def _store_document_ocr(doc, fields, raw_text, quality):
    doc.ocr_text = raw_text or ''
    doc.ocr_fields_json = json.dumps(fields or {}, default=str)
    doc.ocr_quality = quality
    doc.ocr_ran_at = timezone.now()
    doc.save(update_fields=['ocr_text', 'ocr_fields_json', 'ocr_quality', 'ocr_ran_at'])


# ─── Per-Item ECDT Formula ────────────────────────────────────────────────────

def compute_ecdt(items_data, exchange_rate, usd_exchange_rate=None,
                 arrastre=0, wharfage=0, csf_php=0, bank_charges=0):
    """
    items_data keys: exw_usd, freight_usd, insurance_usd, duty_rate,
                     description, quantity, hs_code_id, gw, nw, pkgs
    D/V = EXW + Freight + Insurance  (no auto-3% O/C — matches client CDT tool)
    Total Landed Cost excludes VAT; VAT = 12% of Total Landed Cost
    Brokerage Fee: tiered table up to ₱200,000, then +0.125% of excess
    """
    usd_exchange_rate = Decimal(str(usd_exchange_rate or exchange_rate))
    computed_items = []
    total_dv_php   = Decimal('0')
    total_cud      = Decimal('0')

    for i, item in enumerate(items_data):
        exw            = Decimal(str(item['exw_usd']))
        item_freight   = Decimal(str(item.get('freight_usd',   0) or 0))
        item_insurance = Decimal(str(item.get('insurance_usd', 0) or 0))
        duty_rate      = Decimal(str(item.get('duty_rate',     0) or 0))

        # EXW follows invoice currency; freight/insurance are always USD.
        exw_php       = exw * exchange_rate
        freight_php   = item_freight * usd_exchange_rate
        insurance_php = item_insurance * usd_exchange_rate
        dv_php        = exw_php + freight_php + insurance_php
        dv_usd_equiv  = dv_php / usd_exchange_rate if usd_exchange_rate else Decimal('0')
        cud     = dv_php * (duty_rate / Decimal('100'))
        total_dv_php += dv_php
        total_cud    += cud

        computed_items.append({
            'no':             i + 1,
            'description':    item.get('description', ''),
            'quantity':       item.get('quantity', ''),
            'unit':           item.get('unit', ''),
            'unit_price':     item.get('unit_price', ''),
            'hs_code_id':     item.get('hs_code_id', ''),
            'hs_code':        item.get('hs_code', ''),
            'duty_rate':      float(duty_rate),
            'exw':            float(round(exw, 2)),
            'item_freight':   float(round(item_freight, 2)),
            'item_insurance': float(round(item_insurance, 2)),
            'dv_usd':         float(round(dv_usd_equiv, 2)),
            'dv_php':         float(round(dv_php, 2)),
            'cud':            float(round(cud, 2)),
            'gw':             item.get('gw', ''),
            'nw':             item.get('nw', ''),
            'pkgs':           item.get('pkgs', ''),
        })

    taxable_value   = round(total_dv_php, 2)
    customs_duties  = round(total_cud, 2)
    brokerage_fee   = get_brokerage_fee(taxable_value)
    cds             = Decimal('130')
    ipf             = get_ipf(taxable_value)

    arrastre_d      = Decimal(str(arrastre     or 0))
    wharfage_d      = Decimal(str(wharfage     or 0))
    csf_d           = Decimal(str(csf_php      or 0))
    bank_charges_d  = Decimal(str(bank_charges or 0))

    # Total Landed Cost = DV + Bank Charges + CUD + BF + Arrastre + Wharfage + CDS + IPF
    # NOTE: CSF is NOT included in TLC — it appears only in the BOC fees total (FCL)
    total_landed_cost = round(
        taxable_value + bank_charges_d + customs_duties + brokerage_fee
        + cds + ipf + arrastre_d + wharfage_d, 2
    )

    # VAT = 12% of Total Landed Cost (matches client CDT Excel convention)
    vat = round(total_landed_cost * Decimal('0.12'), 2)

    # BOC total = CUD + VAT + CDS + IPF + CSF (for FCL).
    # Verified from RTripleJ ECDT_FCL.xlsx: CSF appears in the SUMMARY/TOTAL column.
    # For LCL/Air, csf_d = 0 so this formula is safe across all modes.
    boc_total = round(customs_duties + vat + cds + ipf + csf_d, 2)

    summary = {
        'taxable_value':    taxable_value,
        'bank_charges':     bank_charges_d,
        'customs_duties':   customs_duties,
        'brokerage_fee':    brokerage_fee,
        'cds':              cds,
        'ipf':              ipf,
        'arrastre':         arrastre_d,
        'wharfage':         wharfage_d,
        'csf_php':          csf_d,
        'total_landed_cost': total_landed_cost,
        'vat_base':         total_landed_cost,   # stored as vat_base in model
        'vat':              vat,
        'boc_total':        boc_total,
    }
    return computed_items, summary


# ─── OCR Merge Helpers ───────────────────────────────────────────────────────

# Priority order per field: which document type to prefer when the same field
# appears in more than one document.
