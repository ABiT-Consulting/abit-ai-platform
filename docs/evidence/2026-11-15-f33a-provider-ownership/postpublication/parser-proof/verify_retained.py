from pathlib import Path
from datetime import datetime,timezone
import hashlib,json,subprocess
sha=lambda b:hashlib.sha256(b).hexdigest()
ROOT=Path('/srv/abit-dev-linux/abit-ai/deployment/agentic-governance-full-20261113')
OBS=ROOT/'governed-github-commit-readback-recovery-v1-proposal-20261115/returnedf33-current-feed-readonly-observe.private.json'
DIAG=OBS.parent/'returnedf33-visible-contract-diagnostic.json'
PROOF=Path('/var/lib/abit-ai-agentic-gateway/returned-reply-format/agov-parent-f33a6a0515b70ccd0f31bbf872851b4184ddc15327c42a35.json')
FEED=Path('/srv/abit-dev-linux/relay-runtime/service/aibit_dot_feed.cjs')
expected={OBS:'881b0fced1df3e8d8ec649f53c3f5cb7076f2c2a454cf940834bbe02ad24fd6f',DIAG:'8da20632157d3f8d98dc2d5ca8e94e265460cf1bc0b3246b0c04d6e896db1ddd',PROOF:'5b0af71eddfd8b470f8d791576512c6d3e286de065c76522b751e120ae9605e9',FEED:'ddf85a4edd9fc3e71ff41c28c76f670906cb4a7bf11c1185dfd0b9de07e51f53'}
for p,h in expected.items():assert sha(p.read_bytes())==h
o=json.loads(OBS.read_bytes());d=json.loads(DIAG.read_bytes());p=json.loads(PROOF.read_bytes())
lines=[s for s in o['observed']['body_tail'].splitlines() if s.startswith('AGOV RESULT:')]
assert len(lines)==1
line=lines[0];row=o['observed']['feed'][-1]
assert row['id']==p['original_reply_id']==d['visible_assistant_message_id']
assert o['root_message_id']==p['original_root_id']==d['root_message_id']
checks={'original_visible_hash_matches_selected_proof':sha(line.encode())==p['original_visible_reply_sha256'],
 'original_visible_hash_matches_retained_diagnostic':sha(line.encode())==d['visible_contract_line_sha256'],
 'original_line_prefix_matches_row':line.startswith(row['prefix']),
 'original_line_tail_matches_row':line.endswith(row['tail']),
 'original_root_and_reply_exact_match':True,
 'later_correction_has_distinct_root':p['correction_root_id']!=p['original_root_id'],
 'later_correction_has_distinct_reply':p['correction_reply_id']!=p['original_reply_id']}
assert all(checks.values())
try:json.loads(line[len('AGOV RESULT:'):].strip());py={'valid':True}
except json.JSONDecodeError as e:py={'valid':False,'error_class':type(e).__name__,'position':e.pos,'character_codepoint':ord(e.doc[e.pos]) if e.pos<len(e.doc) else None}
cases=[{'name':'retained_original','text':line},{'name':'retained_later_correction','text':p['corrected_reply_utf8']}]
js="""const fs=require('node:fs'), crypto=require('node:crypto');const input=JSON.parse(fs.readFileSync(0,'utf8'));let out=[];for(const row of input){let r={name:row.name,bytes:Buffer.byteLength(row.text,'utf8'),sha256:crypto.createHash('sha256').update(row.text,'utf8').digest('hex')};try{const v=JSON.parse(row.text.slice('AGOV RESULT:'.length).trim());r.json_valid=true;r.object=!!v&&typeof v==='object'&&!Array.isArray(v);}catch(e){r.json_valid=false;r.error_class=e.name;}out.push(r);}process.stdout.write(JSON.stringify(out));"""
r=subprocess.run(['node','-e',js],input=json.dumps(cases).encode(),capture_output=True,timeout=5,check=True)
assert not r.stderr
result={'schema_version':1,'checked_utc':datetime.now(timezone.utc).isoformat(),'purpose':'Pure parsing of exact retained original bytes; no new observation or response edit','references':[{'path':str(k),'sha256':v,'bytes':k.stat().st_size} for k,v in expected.items()],'identity_checks':checks,'original_line_bytes':len(line.encode()),'original_line_sha256':sha(line.encode()),'python_parse':py,'node_parse':json.loads(r.stdout),'node_exit':r.returncode,'live_feed_catches_JSON_parse_failure':"const result = JSON.parse(json)" in FEED.read_text(),'observation_result_was_no_verified_reply':o['dot_handoff'] is False and not o['response'] and o['reply_message_id'] is None,'no_new_actions':{'endpoint_calls':0,'database_accesses':0,'database_writes':0,'model_sends':0,'browser_calls':0},'limits':['This proves a concrete strict-JSON rejection in the retained original visible line; it does not prove the entire rendered DOM or the only rejection condition.','The valid later correction belongs to a distinct user/root and is not an original provider receipt.','No current browser state or permission to persist either response is inferred.']}
print(json.dumps(result,sort_keys=True))
