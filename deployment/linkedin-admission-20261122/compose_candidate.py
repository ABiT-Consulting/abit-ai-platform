"""Compose the exact additive candidate; never imports or changes the live app."""
from pathlib import Path
import ast, difflib, hashlib, json

ROOT = Path(__file__).resolve().parents[2]
PKG = Path(__file__).resolve().parent
BASE = PKG / 'baseline/open_webui'
OUT = PKG / 'candidate/open_webui'

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def once(text, old, new):
    if text.count(old) != 1:
        raise RuntimeError('Expected one exact source boundary: ' + old[:120])
    return text.replace(old, new, 1)

sources = {rel: (BASE / rel).read_text(encoding='utf-8') for rel in ['utils/automations.py', 'models/automations.py', 'routers/automations.py', 'utils/agentic_maintenance.py', 'frontend/agent-activity.js']}
candidate = dict(sources)

s = candidate['models/automations.py']
s = once(s, 'self, now_ns: int, limit: int = 10, db: Optional[AsyncSession] = None\n    ) -> list[tuple[AutomationModel, str]]:',
    'self, now_ns: int, limit: int = 10, db: Optional[AsyncSession] = None,\n        *, excluded_ids: Optional[set[str]] = None,\n    ) -> list[tuple[AutomationModel, str]]:')
s = once(s, "            can_start = or_(~same_work, ~running)\n",
    "            can_start = or_(~same_work, ~running)\n"
    "            # Read before claiming: an unresolved original is not a new attempt.\n"
    "            admitted_reference = ~Automation.id.in_(sorted(excluded_ids or ()))\n")
s = once(s, "                    can_start,\n                )\n                .order_by(Automation.next_run_at)",
    "                    can_start,\n                    admitted_reference,\n                )\n                .order_by(Automation.next_run_at)")
s = once(s, "                        can_start,\n                    )\n                    .values(last_run_at=now_ns",
    "                        can_start,\n                        admitted_reference,\n                    )\n                    .values(last_run_at=now_ns")
candidate['models/automations.py'] = s

s = candidate['utils/automations.py']
s = once(s, 'from open_webui.utils import agentic_maintenance as _agov_maintenance\n',
    'from open_webui.utils import agentic_maintenance as _agov_maintenance\n'
    'from open_webui.utils.automation_admission import blocked_automation_references, denial_status\n')
s = once(s, '    """A denied admission never spawned a worker; finish only that new claim."""',
    '    """Keep the final atomic guard; a lost admission race is not an executed failure."""')
s = once(s, 'lambda: execute_scheduled_automation(app, automation, run_id), automation.id\n',
    'lambda: execute_scheduled_automation(app, automation, run_id), automation.id, run_id=run_id\n')
s = once(s,
    "    except _agov_maintenance.MaintenanceDenied:\n"
    "        await AutomationRuns.finish_claim(\n"
    "            run_id, 'error', error='Maintenance admission denied before execution; original work retained'\n"
    "        )\n"
    "        log.warning('Scheduled automation %s was not started: maintenance admission denied', automation.id)",
    "    except _agov_maintenance.MaintenanceDenied as exc:\n"
    "        denied = denial_status(automation.id, exc)\n"
    "        await AutomationRuns.finish_claim(\n"
    "            run_id, 'admission_denied',\n"
    "            error='[' + denied['code'] + '] ' + denied['reason'] + ' Next action: ' + denied['next_action'],\n"
    "        )\n"
    "        log.warning('Scheduled automation %s claim %s was not started: %s', automation.id, run_id, denied['code'])")
s = once(s, '    next_scheduler_poll = 0.0\n', '    next_scheduler_poll = 0.0\n    last_admission_block = None\n')
s = once(s, "                        await AutomationRuns.expire_claims(now_ns)\n",
    "                        await AutomationRuns.expire_claims(now_ns)\n"
    "                        excluded_ids = blocked_automation_references()\n")
s = once(s, "batch = await Automations.claim_due(now_ns, limit=10, db=db)",
    "batch = await Automations.claim_due(now_ns, limit=10, db=db, excluded_ids=excluded_ids)")
s = once(s,
    "                            await _spawn_claimed_automation(app, automation, run_id)\n"
    "                except Exception:\n"
    "                    log.exception('Scheduler: automation error')",
    "                            await _spawn_claimed_automation(app, automation, run_id)\n"
    "                        last_admission_block = None\n"
    "                except _agov_maintenance.MaintenanceDenied as exc:\n"
    "                    # Log a transition once; polling is observational until admission changes.\n"
    "                    reason = str(exc)\n"
    "                    if reason != last_admission_block:\n"
    "                        log.warning('Scheduler waiting for maintenance admission: %s', reason)\n"
    "                    last_admission_block = reason\n"
    "                except Exception:\n"
    "                    log.exception('Scheduler: automation error')")
candidate['utils/automations.py'] = s

s = candidate['utils/agentic_maintenance.py']
s = once(s,
    'def acquire(kind, reference, *, db_path=None, parent=None, recovery_receipt=None, governance_project=None):',
    'def acquire(kind, reference, *, db_path=None, parent=None, recovery_receipt=None, governance_project=None, automation_run_id=None):')
s = once(s, "        gate = db.execute('SELECT * FROM gate WHERE id=1').fetchone()\n        transferred = False",
    "        gate = db.execute('SELECT * FROM gate WHERE id=1').fetchone()\n"
    "        if automation_run_id is not None:\n"
    "            if kind != 'automation' or not isinstance(automation_run_id, str) or not 0 < len(automation_run_id) <= 200:\n"
    "                raise MaintenanceDenied('Scheduled original run identity differs')\n"
    "            try:\n"
    "                with _readonly(_root(database) / 'webui.db') as originals:\n"
    "                    original = originals.execute(\n"
    "                        'SELECT automation_id,status FROM automation_run WHERE id=?', (automation_run_id,)\n"
    "                    ).fetchone()\n"
    "            except (sqlite3.Error, OSError):\n"
    "                raise MaintenanceDenied('Scheduled original run evidence is unavailable') from None\n"
    "            if not original or original['automation_id'] != reference or original['status'] != 'running':\n"
    "                raise MaintenanceDenied('Scheduled original run identity differs')\n"
    "        transferred = False")
s = once(s, "                'original_receipt': recovery_receipt.digest if recovering else None,\n",
    "                'original_receipt': recovery_receipt.digest if recovering else None,\n"
    "                **({'run_id': automation_run_id} if automation_run_id is not None else {}),\n")
s = once(s, 'def spawn_automation(factory, automation_id):', 'def spawn_automation(factory, automation_id, *, run_id=None):')
s = once(s,
    "lease = acquire('automation', automation_id, parent=parent, db_path=parent.database if parent else None)",
    "lease = acquire('automation', automation_id, parent=parent, db_path=parent.database if parent else None, automation_run_id=run_id)")
candidate['utils/agentic_maintenance.py'] = s

s = candidate['routers/automations.py']
s = once(s,
    "    except _agov_maintenance.MaintenanceDenied:\n"
    "        raise HTTPException(status_code=503, detail='Maintenance admission is closed; original work is preserved') from None",
    "    except _agov_maintenance.MaintenanceDenied as exc:\n"
    "        from open_webui.utils.automation_admission import denial_status\n"
    "        denied = denial_status(automation.id, exc)\n"
    "        # Keep detail:string compatible with existing run-button clients.\n"
    "        raise HTTPException(\n"
    "            status_code=503,\n"
    "            detail='[' + denied['code'] + '] ' + denied['reason'] + ' Next action: ' + denied['next_action'],\n"
    "        ) from None")
s += (PKG / 'admission-route.fragment').read_text(encoding='utf-8')
candidate['routers/automations.py'] = s

s = candidate['frontend/agent-activity.js']
s = once(s, '    const state = String(run?.status || "unknown").toLowerCase();',
    '    const legacyDenial = run?.error === "Maintenance admission denied before execution; original work retained";\n'
    '    const state = legacyDenial ? "admission_denied" : String(run?.status || "unknown").toLowerCase();')
s = once(s, 'const labels = {running:', 'const labels = {admission_denied:"Waiting for admission",running:')
s = once(s, '      deferred:"This attempt was postponed.',
    '      admission_denied:"Admission was denied before execution. No new worker was started.",\n'
    '      deferred:"This attempt was postponed.')
s = once(s, 'next: stale || ["deferred","error","failed","blocked"].includes(state)',
    'next: state === "admission_denied" ? "Read the current admission reason and original-work evidence above. Do not submit a duplicate." : stale || ["deferred","error","failed","blocked"].includes(state)')
s = once(s, '  function render(id, runs, automation) {', '  function render(id, runs, automation, admission) {')
s = once(s, '    const latest = runs[0], info = describe(latest);',
    '    admission = automation?.meta?.governance_managed ? null : admission;\n'
    '    const latest = runs[0];\n'
    '    const blocked = admission && ["waiting","blocked"].includes(admission.state);\n'
    '    const info = blocked ? {label:admission.state === "waiting" ? "Waiting" : "Blocked", reason:admission.reason, next:admission.next_action, tone:"#d99a21"} : describe(latest);')
s = once(s, '    panel.append(node("p",info.reason),node("p","Next action: "+info.next));',
    '    panel.append(node("p",info.reason),node("p","Next action: "+info.next));\n'
    '    if (admission) {\n'
    '      const evidence=node("p",admission.evidence || ""); evidence.dataset.admissionEvidence="true"; panel.append(evidence);\n'
    '      if (admission.gate_epoch != null) panel.append(node("small","Admission epoch: "+admission.gate_epoch+" · "+admission.code));\n'
    '      if (admission.operator_owner) panel.append(node("p","Maintenance owner: "+admission.operator_owner));\n'
    '      if (admission.original_run_id) {\n'
    '        const loaded = runs.some(run => run.id === admission.original_run_id);\n'
    '        const original=node(loaded ? "a" : "p","Original run: "+admission.original_run_id);\n'
    '        if (loaded) {\n'
    '          original.href="/automations/"+encodeURIComponent(id)+"#run-"+encodeURIComponent(admission.original_run_id); original.style.textDecoration="underline";\n'
    '          original.addEventListener("click",()=>{const row=document.getElementById("run-"+admission.original_run_id);const history=row?.closest("details");if(history)history.open=true;});\n'
    '        }\n'
    '        panel.append(original);\n'
    '      }\n'
    '      if (typeof admission.original_chat_url === "string" && /^\\/c\\/[a-z0-9-]{1,100}$/i.test(admission.original_chat_url)) {\n'
    '        const output=node("a","Open original saved conversation"); output.href=admission.original_chat_url; output.style.cssText="display:block;text-decoration:underline"; panel.append(output);\n'
    '      }\n'
    '    }')
s = once(s, '      const d=describe(run),row=node("div");row.style.cssText=',
    '      const d=describe(run),row=node("div");row.id="run-"+run.id;row.style.cssText=')
s = once(s,
    '      const [response, metadata]=await Promise.all([fetch("/api/v1/automations/"+id+"/runs?skip=0&limit=20",options),fetch("/api/v1/automations/"+id,options)]);',
    '      const [response, metadata, admissionResponse]=await Promise.all([fetch("/api/v1/automations/"+id+"/runs?skip=0&limit=20",options),fetch("/api/v1/automations/"+id,options),fetch("/api/v1/automations/"+id+"/admission",options)]);')
s = once(s,
    '      const [runs, automation]=await Promise.all([response.json(),metadata.json()]);\n'
    '      if(version===generation&&Array.isArray(runs)&&automation.id===id)render(id,runs,automation);',
    '      const [runs, automation]=await Promise.all([response.json(),metadata.json()]);\n'
    '      const admission = admissionResponse.ok ? await admissionResponse.json() : {state:"blocked",code:"admission_unavailable",reason:"Current admission could not be verified.",next_action:"Refresh admission evidence or ask the existing run owner to reconcile it. Preserve the original work.",evidence:"The status read did not start a worker."};\n'
    '      if(version===generation&&Array.isArray(runs)&&automation.id===id)render(id,runs,automation,admission);')
candidate['frontend/agent-activity.js'] = s
(ROOT / 'static/agent-activity.js').write_text(s, encoding='utf-8', newline='\n')

candidate['utils/automation_admission.py'] = (ROOT / 'backend/open_webui/utils/automation_admission.py').read_text(encoding='utf-8')
candidate['models/automation_admission.py'] = (ROOT / 'backend/open_webui/models/automation_admission.py').read_text(encoding='utf-8')

manifest = {'schema_version':1, 'agent_id':'d4a6e561-cb2a-41d9-93b5-2888624cf8d6',
 'runtime_root':'/srv/abit-dev-linux/abit-ai/current/runtime/open_webui',
 'authoritative_checkout':r'C:\Code\open-webui-custom', 'branch':'abit-projects',
 'deployment_approved':False, 'source_baseline_note':'Normally readable live source snapshot; no gate or private ledger copied.',
 'files':[]}
patches=[]
for rel, content in sorted(candidate.items()):
    target=OUT/rel;target.parent.mkdir(parents=True,exist_ok=True)
    raw=content.encode('utf-8');target.write_bytes(raw)
    if rel.endswith('.py'):ast.parse(content,filename=rel)
    before=(BASE/rel).read_bytes() if (BASE/rel).is_file() else None
    manifest['files'].append({'path':rel,'expected_before_sha256':sha(before) if before is not None else None,'after_sha256':sha(raw),'bytes':len(raw)})
    patches.extend(difflib.unified_diff((before.decode('utf-8') if before is not None else '').splitlines(keepends=True),
        content.splitlines(keepends=True),fromfile='a/'+rel if before is not None else '/dev/null',tofile='b/'+rel))
(PKG/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8',newline='\n')
(PKG/'additive.patch').write_text(''.join(patches),encoding='utf-8',newline='\n')
print(json.dumps({'candidate_files':len(candidate),'manifest_sha256':sha((PKG/'manifest.json').read_bytes()),'live_mutations':0},indent=2))
