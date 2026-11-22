from pathlib import Path
import hashlib,json,os,sys,subprocess,types,ipaddress
src=Path('/srv/abit-dev-linux/abit-ai-native-overlay-original-finalize-20261122/deployment/native-overlay-original-finalize-20261122/finalize_original.py')
raw=src.read_bytes()
if hashlib.sha256(raw).hexdigest()!='b112638b0ce94816b7b2493bd61de1da7b6c57e40c21113beaeff25109b1452b':raise RuntimeError('SOURCE_DRIFT')
def audit(event,args):
 if event=='open' and isinstance(args[2],int) and args[2]&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):raise RuntimeError('WRITE_DENIED')
 if event=='subprocess.Popen' and list(args[1])!=['/usr/sbin/nft','-s','-j','-a','list','ruleset']:raise RuntimeError('COMMAND_DENIED')
 if event.startswith('socket.') or event in ('os.remove','os.rename','os.mkdir','os.rmdir','os.chmod','os.chown','os.link','os.symlink','os.truncate','os.system'):raise RuntimeError('MUTATION_DENIED')
sys.addaudithook(audit)
x=types.ModuleType('readonly_new_delta');exec(compile(raw,str(src),'exec'),x.__dict__)
b=Path('/var/lib/abit-ai-native-overlay-held-drain-source-20261122/relay_bootstrap.py');br=x.root_read(b)
if x.digest(br)!='b6292e09d4313d6fecca0e63440b5d3f557a0b976a795bbdef93fb42024bd282':raise RuntimeError('SOURCE_DRIFT')
n=types.ModuleType('readonly_nft');exec(compile(br,str(b),'exec'),n.__dict__)
before_raw=x.root_read(x.BEFORE,0o600)
if x.digest(before_raw)!=x.BEFORE_SHA:raise RuntimeError('BASELINE_DRIFT')
baseline=json.loads(before_raw)
other,owned=n.split_ruleset(n.read_ruleset());delta=x.additive_delta(n,baseline,other)
semantic=n.without_handles(other)
target=[v for v in semantic if n.digest(v)==x.KNOWN_DOCKER_LOOPBACK_RULE_SHA]
if len(target)!=1:raise RuntimeError('EXACT_OBSERVED_LOOPBACK_RULE_ABSENT')
row=target[0]['rule']
def matches(row):
 return {n.canonical(v['match']['left']).decode():(v['match']['op'],v['match'].get('right')) for v in row['expr'] if isinstance(v,dict) and isinstance(v.get('match'),dict)}
m=matches(row);ipkey=n.canonical({'payload':{'protocol':'ip','field':'daddr'}}).decode()
portkey=n.canonical({'payload':{'protocol':'tcp','field':'dport'}}).decode()
ifkey=n.canonical({'meta':{'key':'iifname'}}).decode()
if ipkey not in m or portkey not in m or m.get(ifkey)!=('!=','lo'):raise RuntimeError('LOOPBACK_RULE_SHAPE')
if m[ipkey][0]!='==' or not ipaddress.ip_address(m[ipkey][1]).is_loopback:raise RuntimeError('LOOPBACK_DESTINATION')
if m[portkey][0]!='==' or type(m[portkey][1]) is not int or not 1<=m[portkey][1]<=65535:raise RuntimeError('LOOPBACK_PORT')
if row['expr'][-1]!={'drop':None}:raise RuntimeError('LOOPBACK_DROP')
pairs=[]
for v in semantic:
 rr=v.get('rule',{})
 if rr.get('table')=='nat' and rr.get('chain')=='DOCKER':
  mm=matches(rr)
  dnat=any(isinstance(z,dict) and isinstance(z.get('xt'),dict) and z['xt'].get('name')=='DNAT' for z in rr['expr'])
  if mm.get(ipkey)==m[ipkey] and mm.get(portkey)==m[portkey] and dnat and x.docker_expression(rr['expr']):pairs.append(n.digest(v))
if len(pairs)!=1:raise RuntimeError('EXACT_DOCKER_LOOPBACK_DNAT_PAIR_REQUIRED')
proof=n.verify_owned(owned,x.MANIFEST_SHA)
after,afterowned=n.split_ruleset(n.read_ruleset())
if other!=after or owned!=afterowned:raise RuntimeError('FIREWALL_CHANGED_DURING_READ')
print(json.dumps({'purpose':'READ_ONLY_ACTUAL_FINAL_FOREIGN_PREDICATE_AND_LOOPBACK_PAIR','source_sha256':x.digest(raw),'baseline_raw_sha256':x.digest(before_raw),'delta':delta,'owned_proof':proof,'foreign_current_sha256':n.digest(other),'pinned_loopback_rule_sha256':x.KNOWN_DOCKER_LOOPBACK_RULE_SHA,'exact_loopback_destination_and_tcp_port':True,'incoming_interface_not_loopback':True,'terminal_drop':True,'matching_docker_dnat_rule_sha256':pairs[0],'same_loopback_destination_and_tcp_port_in_pair':True,'no_addresses_ports_raw_rules_configs_or_credentials_exported':True,'unchanged_during_read':True,'live_mutations':0,'finalizer_entrypoint_or_native_operation_called':False},sort_keys=True))
