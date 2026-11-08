import asyncio
import copy
import json

import httpx
import pytest

from open_webui.utils.erp_task_resolution import (
    AGENT_URL, ASSIGNEE, DETAIL, DURATION, EXECUTION_ID, HOST, PRIMARY, RELAY_STATUS, SECONDARY, USER,
    Blocked, Resolver, Store, agent_url,
)


class ERP:
    def __init__(self):
        self.task = {'name': 'TASK-TEST', 'project': 'PROJ-TEST', 'status': 'Open',
                     '_assign': [ASSIGNEE], AGENT_URL: None}
        self.writes = []
        self.lose_reply = False

    async def get(self, kind, name):
        return copy.deepcopy(self.task if kind == 'Task' else {'name': name})

    async def assigned(self, task):
        return ASSIGNEE in task['_assign']

    async def save(self, task, changes):
        self.writes.append(changes)
        self.task.update(changes)
        if self.lose_reply and changes.get('status') == 'Working':
            self.lose_reply = False
            raise httpx.ReadTimeout('test lost acknowledgement')


class App:
    def __init__(self):
        self.agents = {}
        self.agent_subjects = {}
        self.updates = []

    async def verify_project(self, project, scope):
        assert project['name'] == 'PROJ-TEST'

    async def verify_scope(self, project, scope):
        await self.verify_project(project, scope)

    async def instructions(self, scope):
        return 'Fixture project instructions'

    async def destination(self, run, task):
        return {'project_id': task.get('custom_chatgpt_project_id', 'fixture-project'),
                'name': task.get('custom_chatgpt_project', 'Fixture project')}

    async def ensure_agent(self, run, task):
        values = run['primary'], run['secondary'], run['host'], run['user']
        self.agents.setdefault(run['agent'], values)
        self.agent_subjects[run['agent']] = task.get('subject')
        assert self.agents[run['agent']] == (PRIMARY, SECONDARY, HOST, USER)

    async def progress(self, run):
        self.updates.append(run['progress'])


class Worker:
    def __init__(self, erp, app, outcome):
        self.erp = erp
        self.app = app
        self.outcome = outcome
        self.starts = {}
        self.active = set()
        self.confirm = True

    async def preflight(self, run):
        assert self.app.agents[run['agent']] == (PRIMARY, SECONDARY, HOST, USER)

    async def observe_or_start(self, run, attempt, task):
        assert self.erp.task['status'] in {'Open', 'Working'}
        assert self.erp.task[EXECUTION_ID] == run['agent']
        assert AGENT_URL in self.erp.task
        assert self.erp.task[AGENT_URL] in (None, agent_url(run['agent']))
        assert self.app.agents[run['agent']] == (PRIMARY, SECONDARY, HOST, USER)
        assert attempt['deadline'] - attempt['started'] == DURATION
        assert not self.active - {attempt['id']}
        self.starts.setdefault(attempt['id'], attempt['number'])
        self.active.add(attempt['id'])
        if self.outcome['status'] == 'completed':
            self.active.remove(attempt['id'])
        return self.outcome

    async def stop(self, run, attempt):
        if self.confirm:
            self.active.discard(attempt['id'])
        return {'confirmed': self.confirm, 'evidence': self.outcome.get('evidence')}


@pytest.fixture
def system(tmp_path):
    erp, app = ERP(), App()
    relay = Worker(erp, app, {'status': 'running', 'progress': 'ChatGPT is still working'})
    cli = Worker(erp, app, {'status': 'completed', 'verified': True,
                            'evidence': {'changes': 'fixed.py', 'verification_exit_code': 0}})
    clock = [1_800_000_000]
    path = tmp_path / 'runs.sqlite'

    def start(**options):
        return Resolver(Store(path), erp, app, relay, cli, {'PROJ-TEST': {'folder_id': 'fixture', 'account': 'fixture', 'chatgpt_project': 'fixture-project'}},
                        clock=lambda: clock[0], **options)

    return start, erp, app, relay, cli, clock


def test_immediate_models_waiting_restart_and_review(system):
    async def scenario():
        start, erp, app, relay, cli, clock = system
        worker = start()
        relay.outcome = {'status': 'queued', 'progress': 'Waiting for a worker'}
        await worker.intake('TASK-TEST')
        assert erp.task['status'] == 'Open'
        relay.outcome = {'status': 'running', 'progress': 'ChatGPT is still working'}
        await worker.advance(worker.store.get('TASK-TEST'))
        working = next(write for write in erp.writes if write.get('status') == 'Working')
        assert working[AGENT_URL] == agent_url(erp.task[EXECUTION_ID])
        first = worker.store.get('TASK-TEST')['attempts'][0]
        assert first['started'] == clock[0]
        assert first['deadline'] == clock[0] + DURATION
        assert app.updates[-1] == 'ChatGPT is still working'
        worker = start()
        await worker.intake('TASK-TEST')
        await worker.advance(worker.store.get('TASK-TEST'))
        assert len(app.agents) == len(relay.starts) == 1
        assert worker.store.get('TASK-TEST')['attempts'][0] == first
        relay.outcome = {'status': 'completed', 'findings': 'reproduction and proposed fix'}
        await worker.advance(worker.store.get('TASK-TEST'))
        assert erp.task['status'] == 'Pending Review'
        audit = json.loads(erp.task[DETAIL])
        assert audit['agent'].endswith(erp.task[EXECUTION_ID])
        assert audit['attempts'][0]['findings'] == relay.outcome['findings']
        assert audit['attempts'][0]['evidence']['verification_exit_code'] == 0
        assert len(cli.starts) == 1
        assert not worker.store.pending()
    asyncio.run(scenario())


def test_three_exhausted_and_explicit_reset(system):
    async def scenario():
        start, erp, app, relay, cli, _ = system
        relay.outcome = {'status': 'completed', 'findings': 'advice'}
        cli.outcome = {'status': 'completed', 'verified': False,
                       'evidence': {'changes': 'work in progress', 'verification_exit_code': 1}}
        worker = start()
        await worker.intake('TASK-TEST')
        assert erp.task['status'] == 'Failed'
        audit = json.loads(erp.task[DETAIL])
        assert len(audit['attempts']) == 3
        assert all(row['stop_reason'] and row['finished'] for row in audit['attempts'])
        assert len(relay.starts) == len(cli.starts) == 3
        erp.task['status'] = 'Open'
        await worker.intake('TASK-TEST')
        assert len(relay.starts) == 3
        worker.store.queue_reset('TASK-TEST')
        relay.outcome = {'status': 'waiting'}
        await worker.reset('TASK-TEST')
        run = worker.store.get('TASK-TEST')
        assert run['cycle'] == 2 and run['attempts'][0]['number'] == 1
        assert len(app.agents) == 1 and len(relay.starts) == 4
    asyncio.run(scenario())


def test_timeout_waits_for_both_stops_and_does_not_extend_deadline(system):
    async def scenario():
        start, erp, app, relay, cli, clock = system
        worker = start()
        await worker.intake('TASK-TEST')
        first = worker.store.get('TASK-TEST')['attempts'][0]
        relay.confirm = False
        clock[0] = first['deadline']
        await worker.advance(worker.store.get('TASK-TEST'))
        assert list(relay.starts) == [first['id']]
        assert relay.active == {first['id']}
        assert erp.task['status'] == 'Working'
        assert 'stop confirmation' in app.updates[-1]
        worker = start()
        relay.confirm = True
        await worker.advance(worker.store.get('TASK-TEST'))
        attempts = worker.store.get('TASK-TEST')['attempts']
        assert len(attempts) == 2 and len(relay.active) == 1
        assert attempts[0]['finished'] == attempts[1]['started'] == first['deadline']
        assert attempts[0]['deadline'] == first['deadline']
    asyncio.run(scenario())


def test_lost_erp_write_ack_reconciles_without_a_second_worker_request(system):
    async def scenario():
        start, erp, app, relay, _, _ = system
        worker = start()
        erp.lose_reply = True
        with pytest.raises(httpx.ReadTimeout):
            await worker.intake('TASK-TEST')
        assert erp.task['status'] == 'Working'
        assert len(relay.starts) == 1
        worker = start()
        await worker.advance(worker.store.get('TASK-TEST'))
        assert len(app.agents) == len(relay.starts) == 1
    asyncio.run(scenario())


def test_missing_erp_agent_url_field_blocks_start_until_available(system):
    async def scenario():
        start, erp, app, relay, cli, clock = system
        del erp.task[AGENT_URL]
        worker = start()
        await worker.intake('TASK-TEST')
        run = worker.store.get('TASK-TEST')
        assert run['status'] == erp.task[RELAY_STATUS] == 'Blocked'
        assert 'Agent URL field' in run['progress']
        assert erp.task['status'] == 'Open'
        assert len(app.agents) == 1 and not relay.starts and not cli.starts and not run['attempts']

        erp.task[AGENT_URL] = None
        clock[0] += 15
        await start().advance(run)
        assert erp.task['status'] == 'Working'
        assert erp.task[AGENT_URL] == agent_url(run['agent'])
        assert len(app.agents) == len(relay.starts) == 1
    asyncio.run(scenario())


def test_existing_erp_claim_is_reused_only_when_explicitly_approved(system):
    async def scenario():
        start, erp, app, relay, _, _ = system
        approved = '4d79d64a-8aba-4731-a06d-de271d023d35'
        erp.task[EXECUTION_ID] = approved
        app.agents[approved] = (PRIMARY, SECONDARY, HOST, USER)
        await start().intake('TASK-TEST')
        await start(existing_agents={'TASK-TEST': 'different-agent'}).intake('TASK-TEST')
        assert not relay.starts

        worker = start(existing_agents={'TASK-TEST': approved})
        await worker.intake('TASK-TEST')
        await worker.intake('TASK-TEST')
        assert worker.store.get('TASK-TEST')['agent'] == approved
        assert erp.task[AGENT_URL] == agent_url(approved)
        assert set(app.agents) == {approved} and len(relay.starts) == 1
    asyncio.run(scenario())


def test_task_edit_refreshes_same_agent_without_restarting_attempt(system):
    async def scenario():
        start, erp, app, relay, _, _ = system
        worker = start()
        await worker.refresh('TASK-TEST')
        before = worker.store.get('TASK-TEST')
        erp.task['subject'] = 'Revised scope'
        erp.task['custom_chatgpt_project_id'] = 'revised-project'
        await start().refresh('TASK-TEST')
        assert app.agent_subjects == {before['agent']: 'Revised scope'}
        assert list(relay.starts) == [before['attempts'][0]['id']]
        assert worker.store.get('TASK-TEST') == before
        assert before['attempts'][0]['destination']['project_id'] == 'fixture-project'
    asyncio.run(scenario())


def test_reassigned_while_running_stops_without_overwriting_erp_status(system):
    async def scenario():
        start, erp, _, relay, _, _ = system
        worker = start()
        await worker.intake('TASK-TEST')
        erp.task['_assign'] = ['other@example.test']
        await worker.advance(worker.store.get('TASK-TEST'))
        await worker.advance(worker.store.get('TASK-TEST'))
        assert not relay.active
        assert erp.task['status'] == 'Working'
        assert worker.store.get('TASK-TEST')['status'] == 'Cancelled'
        assert worker.store.get('TASK-TEST')['attempts'][0]['stop_reason']
    asyncio.run(scenario())


def test_unmapped_task_blocks_without_agent(system):
    async def scenario():
        start, erp, app, relay, _, _ = system
        worker = start()
        erp.task['project'] = 'UNKNOWN'
        await worker.intake('TASK-TEST')
        assert not app.agents and not relay.starts
        assert 'No approved' in erp.task[DETAIL]
        assert erp.task['status'] == 'Open'
    asyncio.run(scenario())


def test_recover_verification_finished_before_deadline(system):
    async def scenario():
        start, erp, _, relay, cli, clock = system
        relay.outcome = {'status': 'completed', 'findings': 'advice'}
        cli.outcome = {'status': 'running'}
        worker = start()
        await worker.intake('TASK-TEST')
        first = worker.store.get('TASK-TEST')['attempts'][0]
        clock[0] = first['deadline'] + 2

        async def stopped_with_verified_result(run, attempt):
            cli.active.clear()
            return {'confirmed': True, 'status': 'completed', 'verified': True,
                    'finished_at': first['deadline'] - 1, 'evidence': {'verification_exit_code': 0}}

        cli.stop = stopped_with_verified_result
        await start().advance(worker.store.get('TASK-TEST'))
        assert erp.task['status'] == 'Pending Review'
        assert len(relay.starts) == len(cli.starts) == 1
    asyncio.run(scenario())


def test_erp_retries_transient_reads_without_replaying_writes():
    from open_webui.utils.erp_task_resolution import ERPClient

    async def scenario():
        calls = []

        def respond(request):
            calls.append(request.method)
            if len(calls) == 1:
                raise httpx.ReadTimeout('transient failure', request=request)
            if len(calls) == 2 or request.method == 'POST':
                return httpx.Response(503, json={})
            return httpx.Response(200, json={'data': 'verified'})

        erp = ERPClient.__new__(ERPClient)
        async with httpx.AsyncClient(base_url='https://erp.abit.ai',
                                     transport=httpx.MockTransport(respond)) as client:
            erp.client = client
            assert await erp.request('GET', '/test') == {'data': 'verified'}
            with pytest.raises(httpx.HTTPStatusError):
                await erp.request('POST', '/test')
            assert calls == ['GET', 'GET', 'GET', 'POST']

    asyncio.run(scenario())


def test_reopening_task_reruns_same_agent_once(system):
    async def scenario():
        start, erp, app, relay, _, _ = system
        worker = start()
        await worker.refresh('TASK-TEST')
        relay.outcome = {'status': 'completed', 'findings': 'Plan'}
        await worker.advance(worker.store.get('TASK-TEST'))
        first = worker.store.get('TASK-TEST')
        assert first['status'] == 'Pending Review' and first['synced']
        erp.task['status'] = 'Open'
        relay.outcome = {'status': 'waiting'}
        await worker.refresh('TASK-TEST')
        await worker.refresh('TASK-TEST')
        second = worker.store.get('TASK-TEST')
        assert second['cycle'] == 2 and second['agent'] == first['agent']
        assert len(second['attempts']) == 1 and len(app.agents) == 1 and len(relay.starts) == 2
        assert worker.store.history('TASK-TEST') == [first]

    asyncio.run(scenario())


def test_erp_assignment_uses_explicit_field_lookup_and_open_todo():
    from open_webui.utils.erp_task_resolution import ERPClient

    async def scenario():
        calls = []

        def respond(request):
            calls.append(request)
            if request.url.path == '/api/method/frappe.client.get_value':
                assert request.url.params['filters'] == 'TASK-TEST'
                assert request.url.params['fieldname'] == '_assign'
                return httpx.Response(200, json={'message': {'_assign': json.dumps([ASSIGNEE])}})
            assert request.url.path == '/api/resource/ToDo'
            filters = json.loads(request.url.params['filters'])
            assert ['allocated_to', '=', ASSIGNEE] in filters
            assert ['reference_name', '=', 'TASK-TEST'] in filters
            assert ['status', '=', 'Open'] in filters
            return httpx.Response(200, json={'data': [{'name': 'test-todo', 'reference_name': 'TASK-TEST'}]})

        erp = ERPClient.__new__(ERPClient)
        async with httpx.AsyncClient(base_url='https://erp.abit.ai',
                                     transport=httpx.MockTransport(respond)) as client:
            erp.client = client
            assert await erp.assigned({'name': 'TASK-TEST'})
        assert len(calls) == 2
    asyncio.run(scenario())


def test_blocked_environment_keeps_visible_agent_without_execution(system):
    async def scenario():
        start, erp, app, relay, cli, _ = system
        erp.task[AGENT_URL] = None

        async def incompatible(project, scope):
            raise Blocked('Project is configured for Dev-02')

        app.verify_scope = incompatible
        worker = start()
        await worker.intake('TASK-TEST')
        await start().intake('TASK-TEST')
        state = worker.store.get('TASK-TEST')
        assert state['linked'] and state['status'] == 'Blocked'
        assert state['attempts'] == []
        assert len(app.agents) == 1 and not relay.starts and not cli.starts
        assert erp.task['status'] == 'Open'
        assert erp.task[RELAY_STATUS] == 'Blocked'
        assert erp.task[AGENT_URL].endswith(state['agent'])
        assert json.loads(erp.task[DETAIL])['agent'] == erp.task[AGENT_URL]
    asyncio.run(scenario())


def test_inbox_acknowledgement_preserves_newer_delivery(tmp_path):
    path = tmp_path / 'intake.sqlite'
    store = Store(path)
    store.enqueue('TASK-TEST')
    task, revision = store.inbox()[0]
    restarted = Store(path)
    restarted.enqueue(task)
    store.acknowledge(task, revision)
    assert store.inbox() == [(task, revision + 1)]
    restarted.acknowledge(task, revision + 1)
    assert store.inbox() == []


def test_task_creator_does_not_override_ai_developer_assignment(system):
    async def scenario():
        start, erp, app, relay, _, _ = system
        creator = 'creator@example.test'
        erp.task.update(owner=creator, type='Development', _assign=['another@example.test'])
        worker = start(creator=creator)
        await worker.intake('TASK-TEST')
        assert not app.agents and not relay.starts
        assert worker.store.get('TASK-TEST') is None

    asyncio.run(scenario())
