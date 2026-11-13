"""Resolve workspace scopes from trusted ABiT AI configuration."""


class WorkspaceConfigurationError(ValueError):
    """The configured workspace does not authorize this ERP task."""


def approved_workspace(task, projects, task_workspaces=None, standalone=None):
    """Return the task override, ERP project scope, or approved neutral scope."""
    task_id = task['name']
    project = task.get('project') or None
    project_scope = projects.get(project)
    override = (task_workspaces or {}).get(task_id)
    if override is not None:
        if 'project' not in override or (override['project'] or None) != project:
            raise WorkspaceConfigurationError('The approved task workspace belongs to a different ERP project')
        scope = {**(project_scope or {}), **{key: value for key, value in override.items() if key != 'project'}}
        if not all(scope.get(key) for key in ('owner_id', 'repository', 'workspace', 'branch')):
            raise WorkspaceConfigurationError('The approved task workspace lacks its owner, repository, directory or branch')
        for key in ('folder_id', 'account', 'chatgpt_project'):
            scope.setdefault(key, None)
        scope['workspace_source'] = 'task'
        return scope
    if project_scope:
        return project_scope
    if standalone and task_id in standalone.get('tasks', ()):
        if not standalone.get('owner_id'):
            raise WorkspaceConfigurationError('The standalone workspace has no approved owner')
        return {
            'workspace_source': 'standalone',
            'owner_id': standalone['owner_id'],
            'folder_id': None,
            'account': None,
            'chatgpt_project': None,
            'repository': None,
            'workspace': None,
            'branch': None,
        }
    return None
