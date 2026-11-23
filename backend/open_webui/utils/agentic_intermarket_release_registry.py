"""Two fixed reviewed InterMarket packages; no caller-selected module or path.

Both use one installed broker profile, one original-operations journal and the
physical identity systemd:intermarket-sbodesk.service. Selecting a package does
not grant admission, approval, permission, a worker or a new state root.
"""

RESOURCES = frozenset({'intermarket-pr237', 'intermarket-pr241-auth-guard'})


def release_for(resource):
    if type(resource) is not str:
        raise ValueError('Fixed InterMarket release resource required')
    if resource == 'intermarket-pr237':
        from open_webui.utils import agentic_intermarket_release as release
    elif resource == 'intermarket-pr241-auth-guard':
        from open_webui.utils import agentic_intermarket_pr241_release as release
    else:
        raise ValueError('Unregistered InterMarket release resource')
    return release


def activation_parent(resource):
    # Each immutable package gets only its own publication directory. The
    # service drop-in and original journal/reservation remain shared.
    if type(resource) is str and resource == 'intermarket-pr237':
        return '/var/lib/intermarket-pr237-activation'
    if type(resource) is str and resource == 'intermarket-pr241-auth-guard':
        return '/var/lib/intermarket-pr241-auth-guard-activation'
    raise ValueError('Unregistered InterMarket activation directory')
