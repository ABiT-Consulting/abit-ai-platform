"""Short human activity summaries; original evidence remains available in details."""
from html import escape, unescape
import re

def safe_reason(value, limit=500):
    value = unescape(re.sub(r'<[^>]*>', ' ', str(value or '')))
    value = re.sub(r'\s+', ' ', value).strip()
    value = re.sub(r'(?i)(bearer\s+)[\w.~-]+', r'\1[redacted]', value)
    value = re.sub(r'(?i)((?:token|password|secret|api_key)\s*[:=]\s*)\S+', r'\1[redacted]', value)
    return value[:limit]

def activity_content(label, text):
    plain = unescape(re.sub(r'<[^>]*>', ' ', str(text or '')))
    plain = plain.replace('\\n', '\n')
    lines = [re.sub(r'\s+', ' ', x).strip(' -*') for x in plain.splitlines() if x.strip()]
    def field(prefix):
        return next((x[len(prefix):].strip() for x in lines if x.lower().startswith(prefix.lower())), '')
    reported = field('ERP RESULT:')
    state = field('Status:') or ('Blocked' if reported == 'BLOCKED' else
        'Ready for review' if reported == 'READY FOR REVIEW' else
        'Work continues' if reported == 'CONTINUE' else 'Update')
    def short(value, limit=350):
        value = re.sub(r'(?i)(bearer\s+)[\w.~-]+', r'\1[redacted]', value)
        value = re.sub(r'(?i)((?:token|password|secret|api_key)\s*[:=]\s*)\S+', r'\1[redacted]', value)
        return value[:limit] + ('…' if len(value) > limit else '')
    excluded = ('ERP RESULT:', 'ERP ISSUES:', 'Status:', 'Evidence:', 'Blocker:', 'Needs:', 'Next:', 'Tried:')
    summary = field('Blocker:') if state.lower() == 'blocked' else ''
    summary = summary or next((x for x in lines if not x.startswith(excluded) and not x.startswith(('{', '['))), '')
    next_action = field('Next:') or field('Needs:')
    if not next_action:
        next_action = ('Resolve the blocker, then reconcile the saved run before retrying.' if state.lower() == 'blocked'
            else 'Independent review and acceptance checks are required.' if state == 'Ready for review'
            else 'Review the saved conversation for the next step.')
    result = '<p><strong>' + escape(short(state, 80)) + '</strong></p>'
    if summary: result += '<p>' + escape(short(summary)) + '</p>'
    result += '<p><strong>Next action:</strong> ' + escape(short(next_action)) + '</p>'
    evidence = field('Evidence:')
    if evidence: result += '<p><strong>Verification:</strong> ' + escape(short(evidence)) + '</p>'
    # Preserve the existing run marker for duplicate-safe comment reconciliation.
    result += '<details><summary>Technical details</summary><p>' + escape(label) + '</p><pre>'
    result += escape(short(str(text or ''), 6000)) + '</pre></details>'
    return result
