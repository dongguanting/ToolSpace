# Copyright 2026 Zapier, Inc.
# SPDX-License-Identifier: MIT
"""Cross-service checks for references to real workflow outputs.

Generated identifiers are resolved from the world, never pinned to a reference
trajectory. Counts include BCC so extra recipients cannot bypass scope checks.
"""
import re
from email.utils import parseaddr

from verifiers.registry import AssertionRegistry


def sent(world):
    return [m for m in world.gmail.messages if 'SENT' in m.label_ids]


def addresses(values):
    return {parseaddr(v)[1].lower() for v in values}


def messages(world, a):
    return [m for m in sent(world)
            if a['to'].lower() in addresses(m.to)
            and a.get('subject_contains', '').casefold() in (m.subject or '').casefold()
            and _includes(m.body_plain, a.get('body_contains'))]


@AssertionRegistry.register('workflow_sent_scope')
def sent_scope(world, a):
    """Check the complete sent-message recipient multiset, including CC/BCC."""
    actual = {}
    for m in sent(world):
        recipients = addresses(m.to + m.cc + m.bcc)
        if len(m.to) != 1 or len(recipients) != 1 or m.cc or m.bcc:
            return False
        recipient = next(iter(recipients))
        actual[recipient] = actual.get(recipient, 0) + 1
    return actual == a['recipients']


@AssertionRegistry.register('workflow_slack_count')
def slack_count(world, a):
    channels = {c.id for c in world.slack.channels if c.name.lstrip('#') == a['channel_name'].lstrip('#')}
    return sum(m.channel_id in channels for m in world.slack.messages) == a['count']


@AssertionRegistry.register('workflow_spreadsheet_email_reference')
def spreadsheet_email_reference(world, a):
    """Require a populated candidate row and its actual spreadsheet URL in email."""
    for sheet in world.google_sheets.spreadsheets:
        if sheet.title != a['title']:
            continue
        rows = [r for r in world.google_sheets.rows if r.spreadsheet_id == sheet.id
                and a['candidate'] in r.cells.values()]
        if len(rows) != 1:
            continue
        text = '\n'.join(str(v) for v in rows[0].cells.values()).casefold()
        if any(v.casefold() in text for v in a.get('row_not_contains', [])) or not all(v.casefold() in text for v in a['row_contains']):
            continue
        url = 'https://docs.google.com/spreadsheets/d/' + sheet.id
        for m in messages(world, a):
            if not _includes(m.subject, a.get('email_subject_contains')) or not _includes(m.body_plain, a.get('email_contains')):
                continue
            if any(v.casefold() in (m.body_plain or '').casefold() for v in a.get('email_not_contains', [])):
                continue
            # A full URL token must match, not an ID prefix followed by junk.
            urls = re.findall(r'https://docs\.google\.com/spreadsheets/d/[A-Za-z0-9_-]+', m.body_plain or '')
            if url in urls:
                return True
    return False


@AssertionRegistry.register('workflow_jira_email_reference')
def jira_email_reference(world, a):
    """Match one employee's created issue to their email entry, without fixed IDs."""
    issues = [r for r in world.jira.actions.get('create_issue', [])
              if r.params.get('project') == a['project']
              and r.params.get('summary') == a['summary']]
    if len(issues) != 1:
        return False
    issue = issues[0]
    description = str(issue.params.get('description', '')).casefold()
    if not all(v.casefold() in description for v in a['description_contains']):
        return False
    if a.get('total_issues') is not None and len(world.jira.actions.get('create_issue', [])) != a['total_issues']:
        return False
    key = f"{a['project']}-{issue.id[-5:]}"
    # Require the employee and actual key in the same paragraph/line entry.
    # This accepts prose or tables, but rejects swapped per-employee mappings.
    for m in messages(world, a):
        for block in re.split(r'\n\s*\n', m.body_plain or '') + (m.body_plain or '').splitlines():
            if a['employee'] in block and a['employee_id'] in block and re.search(r'(?<![\w-])' + re.escape(key) + r'(?![\w-])', block):
                if not any(other != a['employee_id'] for other in re.findall(r'EMP-\d+', block)):
                    return True
    return False


@AssertionRegistry.register('workflow_docusign_email_reference')
def docusign_email_reference(world, a):
    envelopes = [e for e in world.docusign.envelopes
                 if e.template_id == a['template_id'] and e.status == 'sent'
                 and len(e.signers) == 1 and e.signers[0].email.lower() == a['to'].lower()]
    if len(envelopes) != 1 or len(world.docusign.envelopes) != 1:
        return False
    envelope = envelopes[0]
    identifier = getattr(envelope, 'envelope_id', None) or envelope.id
    if a.get('sent_scope') and not sent_scope(world, {'recipients': a['sent_scope']}):
        return False
    def sent_before(m):
        order = getattr(world, '_workflow_order', {})
        left, right = order.get(('docusign', identifier)), order.get(('gmail', m.id))
        if left is not None and right is not None:
            return left < right
        return timestamp(envelope.sent_date_time) is not None and timestamp(envelope.sent_date_time) < timestamp(m.internal_date)
    return any(_includes(m.body_plain, a.get('email_contains')) and re.search(r'(?<![\w-])' + re.escape(identifier) + r'(?![\w-])', m.body_plain or '') and sent_before(m) for m in messages(world, a))


@AssertionRegistry.register('workflow_spreadsheet_count')
def spreadsheet_count(world, a):
    sheets = [s for s in world.google_sheets.spreadsheets if s.title == a['title']]
    return len(sheets) == 1 and sum(r.spreadsheet_id == sheets[0].id and list(r.cells.values()) != ['Candidate', 'Research'] for r in world.google_sheets.rows) == a['rows']


def timestamp(value):
    from datetime import datetime, timezone
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) / 1000
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


@AssertionRegistry.register('workflow_emails_before_summary')
def emails_before_summary(world, a):
    channels = {c.id for c in world.slack.channels if c.name.lstrip('#') == a['channel_name'].lstrip('#')}
    summaries = [m for m in world.slack.messages if m.channel_id in channels]
    relevant = [m for m in sent(world) if addresses(m.to) & set(a['recipients'])]
    times = [timestamp(m.created_at) for m in summaries]
    return bool(relevant) and bool(times) and all(t is not None for t in times) and max(timestamp(m.internal_date) for m in relevant) < min(times)


@AssertionRegistry.register('workflow_email_before_email')
def email_before_email(world, a):
    first = messages(world, a['first'])
    last = messages(world, a['last'])
    # Gmail timestamps have millisecond precision. Messages are appended in send
    # order, which distinguishes separate sends within the same millisecond.
    positions = {id(m): i for i, m in enumerate(world.gmail.messages)}
    def order(m):
        return (m.internal_date, positions[id(m)])
    return bool(first and last) and max(map(order, first)) < min(map(order, last))


@AssertionRegistry.register('workflow_asana_action_order')
def asana_action_order(world, a):
    """Enforce chronological prerequisites using recorded action timestamps."""
    times = []
    for selector in a['sequence']:
        records = [r for r in world.asana.actions.get(selector['action_key'], [])
                   if all(r.params.get(k) == v for k, v in selector['params'].items())]
        if len(records) != 1:
            return False
        t = timestamp(records[0].created_at)
        if t is None:
            return False
        times.append(t)
    return all(left < right for left, right in zip(times, times[1:]))


@AssertionRegistry.register('workflow_asana_task_scope')
def asana_task_scope(world, a):
    from collections import Counter
    records = world.asana.actions.get('create_task', [])
    preserved = set(a.get('preserved_ids', []))
    if not preserved.issubset({r.id for r in records}):
        return False
    return Counter(r.params.get('name') for r in records if r.id not in preserved) == Counter(a['names'])


def _terms(value):
    return value if isinstance(value, list) else [value] if value else []


def _includes(text, value):
    return all(str(v).casefold() in (text or '').casefold() for v in _terms(value))


@AssertionRegistry.register('workflow_email_delivered')
def email_delivered(world, a):
    """One complete business email: recipient, subject, important facts and exclusions."""
    found = [m for m in sent(world)
             if addresses(m.to) == {a['to'].lower()} and len(m.to) == 1
             and not m.cc and not m.bcc
             and _includes(m.subject, a.get('subject_contains'))
             and _includes(m.body_plain, a.get('body_contains'))
             and not any(str(v).casefold() in (m.body_plain or '').casefold()
                         for v in _terms(a.get('body_not_contains')))]
    return len(found) == 1 and (not a.get('sent_scope') or sent_scope(world, {'recipients': a['sent_scope']}))


@AssertionRegistry.register('workflow_email_batch')
def email_batch(world, a):
    """A required batch of employee notifications, checked per recipient."""
    return all(email_delivered(world, spec) for spec in a['emails'])


@AssertionRegistry.register('workflow_slack_summary')
def slack_summary(world, a):
    channels = {c.id for c in world.slack.channels if c.name.lstrip('#') == a['channel_name'].lstrip('#')}
    records = [m for m in world.slack.messages if m.channel_id in channels]
    return len(records) == 1 and _includes(records[0].text, a['text_contains']) and not any(
        str(v).casefold() in (records[0].text or '').casefold() for v in a.get('text_not_contains', []))


@AssertionRegistry.register('workflow_rows_match')
def rows_match(world, a):
    """Check the actual tracker rows, preserving all non-status source fields."""
    for spec in a['rows']:
        records = [r for r in world.google_sheets.rows
                   if r.spreadsheet_id == spec['spreadsheet_id']
                   and r.worksheet_id == spec['worksheet_id'] and r.row_id == spec['row_id']]
        if len(records) != 1 or records[0].cells != spec['cells']:
            return False
    return True


@AssertionRegistry.register('workflow_asana_batch')
def asana_batch(world, a):
    """Verify each task deliverable as a unit, including destination and notes."""
    for spec in a['tasks']:
        params = spec['params']
        records = [r for r in world.asana.actions.get('create_task', [])
                   if r.params.get('name') == params['name']]
        if len(records) != 1:
            return False
        if any(records[0].params.get(k) != v for k, v in params.items()):
            return False
        if not _includes(records[0].params.get('notes'), spec.get('notes_contains')):
            return False
        if not all(AssertionRegistry.check(world, f) for f in spec.get('followups', [])):
            return False
    if a.get('scope') and not asana_task_scope(world, a['scope']):
        return False
    return all(asana_action_order(world, {'sequence': sequence}) for sequence in a.get('orders', []))


@AssertionRegistry.register('workflow_background_requests')
def background_requests(world, a):
    """Real requests for each candidate must precede their panel briefs."""
    for spec in a['requests']:
        if not email_delivered(world, spec['email']):
            return False
        first = [m for m in sent(world) if spec['email']['to'].lower() in addresses(m.to)
                 and _includes(m.body_plain, spec['email']['body_contains'])]
        for target in spec['briefs']:
            last = messages(world, target)
            if not last or max(m.internal_date for m in first) >= min(m.internal_date for m in last):
                return False
    return True
