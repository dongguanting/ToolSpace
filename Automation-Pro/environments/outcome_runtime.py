"""Opt-in field fidelity and same-record outcome checks for the v3 trial only.

Legacy runs never install this adapter. No old assertion behavior or primary
partial-credit formula changes. New outcome_fields are executed, never ignored.
"""
import json,os
from pathlib import Path
from decimal import Decimal,InvalidOperation
_INSTALLED=False
A=Path(__file__).resolve().parent

def _match(actual,expected):
 if isinstance(expected,dict):
  if set(expected)=={'contains'}:
   needles=expected['contains'];needles=needles if isinstance(needles,list) else [needles]
   return isinstance(actual,str) and all(str(v).casefold() in actual.casefold() for v in needles)
  return isinstance(actual,dict) and all(k in actual and _match(actual[k],v) for k,v in expected.items())
 if isinstance(expected,list):return isinstance(actual,list) and len(actual)==len(expected) and all(_match(a,b) for a,b in zip(actual,expected))
 if actual is None or expected is None:return actual is expected
 if isinstance(actual,bool) or isinstance(expected,bool):return actual is expected
 try:
  if Decimal(str(actual))==Decimal(str(expected)):return True
 except (InvalidOperation,ValueError,TypeError):pass
 return str(actual).strip().casefold()==str(expected).strip().casefold()

def install():
 global _INSTALLED
 if _INSTALLED:return
 _INSTALLED=True
 from automationbench.schema.quickbooks import QBLineItem
 from automationbench.tools.api.impl import quickbooks as qb,google_sheets as gs,xero as xr
 from automationbench.tools.api.routes import quickbooks as qb_routes,xero as xr_routes
 original=qb.quickbooks_invoice_create
 def invoice_create(world,body,**kwargs):
  data=json.loads(original(world,body,**kwargs));inv=world.quickbooks.invoices[-1];lines=[]
  for i,line in enumerate(body.get('Line') or [],1):
   detail=line.get('SalesItemLineDetail') or {};item=detail.get('ItemRef') or {}
   lines.append(QBLineItem(line_num=int(line.get('LineNum',i)),amount=Decimal(str(line.get('Amount',0))),description=line.get('Description'),item_id=item.get('value'),item_name=item.get('name'),qty=Decimal(str(detail.get('Qty',1))),unit_price=Decimal(str(detail['UnitPrice'])) if detail.get('UnitPrice') is not None else None,tax_code=(detail.get('TaxCodeRef') or {}).get('value'),service_date=detail.get('ServiceDate')))
  inv.line_items=lines
  bill=body.get('BillEmail')
  if isinstance(bill,dict):inv.bill_email=bill.get('Address') or bill.get('address')
  data['Invoice']=inv.to_display_dict();return json.dumps(data)
 qb.quickbooks_invoice_create=invoice_create;qb_routes.quickbooks_invoice_create=invoice_create
 original_payment=xr.xero_payment_create
 def payment_create(world,body,**kwargs):
  before=len(world.xero.payments);data=json.loads(original_payment(world,body,**kwargs))
  payloads=body.get('Payments',[body]) if 'Payments' in body else [body]
  for p,raw in zip(world.xero.payments[before:],payloads):
   if raw.get('CurrencyRate') is not None:p.currency_rate=Decimal(str(raw['CurrencyRate']))
   if raw.get('Status') is not None:p.status=raw['Status']
  data['Payments']=[p.to_display_dict() for p in world.xero.payments[before:]];return json.dumps(data)
 xr.xero_payment_create=payment_create;xr_routes.xero_payment_create=payment_create
 for key,fn in list(xr_routes._HANDLERS.items()):
  if fn is original_payment:xr_routes._HANDLERS[key]=payment_create
 header_file=A/'sheet_headers.json'
 headers=json.loads(header_file.read_text())['records']
 lookup={(h['spreadsheet_id'],h['worksheet_id'],tuple(sorted(h['headers']))):h['headers'] for h in headers}
 original_headers=gs._effective_headers
 def effective_headers(ws,rows):
  if ws and not ws.headers:
   fields=tuple(sorted({k for r in rows for k in r.cells}))
   original_order=lookup.get((ws.spreadsheet_id,ws.id,fields))
   if original_order:return list(original_order)
  return original_headers(ws,rows)
 gs._effective_headers=effective_headers
 # Some value operations still compute headers locally instead of calling the
 # shared helper. Supply the recovered ordering for the duration of the call,
 # then restore metadata so read-only queries cannot mutate the WorldState.
 from automationbench.tools.api.routes import google_sheets as gs_routes
 for name,fn in list(vars(gs).items()):
  if not name.startswith('google_sheets_values_') or not callable(fn):continue
  def with_headers(world,*args,fn=fn,**kwargs):
   restored=[]
   for ws in world.google_sheets.worksheets:
    if ws.headers:continue
    rows=world.google_sheets.get_rows_for_worksheet(ws.spreadsheet_id,ws.id)
    fields=tuple(sorted({k for row in rows for k in row.cells}))
    known=lookup.get((ws.spreadsheet_id,ws.id,fields))
    if known:restored.append(ws);ws.headers=list(known)
   try:return fn(world,*args,**kwargs)
   finally:
    for ws in restored:ws.headers=[]
  setattr(gs,name,with_headers)
  if hasattr(gs_routes,name):setattr(gs_routes,name,with_headers)
 from verifiers.registry import AssertionRegistry
 import verifiers
 specs={'quickbooks_invoice_exists':('quickbooks','invoices'),'xero_payment_exists':('xero','payments'),'hubspot_contact_exists':('hubspot','contacts'),'hubspot_ticket_exists':('hubspot','tickets')}
 for kind,(service,collection) in specs.items():
  native=AssertionRegistry._handlers[kind]
  def checked(world,assertion,native=native,service=service,collection=collection):
   expected=assertion.get('outcome_fields')
   if expected is None:return native(world,assertion)
   if not isinstance(expected,dict) or not expected:return False
   state=getattr(world,service)
   for record in getattr(state,collection):
    isolated=world.model_copy(update={service:state.model_copy(update={collection:[record]})})
    if native(isolated,assertion) and _match(record.model_dump(mode='json'),expected):return True
   return False
  checked.__name__=native.__name__
  AssertionRegistry._handlers[kind]=checked
