"""Distinct original-only recovery attestation; no original apply renewal.

The separate root recovery holder must prove the original holder ceased,
acquire the same release flock, and retain the original close/nft/browser
keeper identities without recreating them. Native carrier terminal outcome
and full definition restoration are prerequisites. This pure module does not
read credentials, acquire barriers, issue services or finalize the old hold.
"""
from __future__ import annotations
import base64,hashlib,hmac,json
import held_admission_receipt as held
from held_admission_receipt import (Held,need,canonical,sha,equal,unique,instant,utc,SHA)

PURPOSE="native-two-role-leaf-repair-original-recovery-v1"
ACTION="restore_original_or_reconcile"
ADMISSION_SECONDS=120
OPERATION_SECONDS=600
BODY_SECONDS=420
RESERVE_SECONDS=60
MAX_ENVELOPE=49152
DIGESTS={"operation_id","controller_sha256","manifest_sha256","signer_sha256",
 "original_hold_receipt_sha256","original_holder_journal_sha256","original_context_sha256",
 "keeper_ready_sha256","original_observation_sha256","continuity_sha256"}
BINDING_KEYS=DIGESTS|{"original_binding","original_holder","recovery_holder",
 "original_carrier","holder_lease_expires_at"}
PROCESS_KEYS={"pid","start_ticks","uid","cmdline_sha256","source_sha256"}
ORIGINAL_HOLDER_KEYS=PROCESS_KEYS|{"positively_ceased","cessation_proof_sha256"}
RECOVERY_HOLDER_KEYS=PROCESS_KEYS|{"same_release_flock_held","flock_proof_sha256"}
CARRIER_KEYS={"release_id","environment_id","attempt_id","terminal_state","terminal_receipt_sha256",
 "definition_id","restored_revision","definition_restoration_sha256","business_fields_exact"}
OBSERVATION_KEYS={"held_observation","original_holder","recovery_holder","original_carrier",
 "original_holder_journal_sha256","original_context_sha256","keeper_ready_sha256",
 "original_observation_sha256","continuity_sha256","original_barriers_continuously_retained",
 "original_apply_resumed","barriers_recreated"}


def digest_value(value,code):
 need(type(value)is str and SHA.fullmatch(value),code)


def process(value,keys):
 need(type(value)is dict and set(value)==keys,"EXACT_RECOVERY_HOLDER_PROCESS")
 for name in ("pid","start_ticks"):
  need(type(value[name])is int and 1<value[name]<2**63,"EXACT_RECOVERY_HOLDER_BIRTH")
 need(type(value["uid"])is int and value["uid"]==0,"ROOT_RECOVERY_HOLDER_REQUIRED")
 for name in ("cmdline_sha256","source_sha256"):digest_value(value[name],"EXACT_RECOVERY_PROCESS_DIGEST")


def validate_binding(binding):
 need(type(binding)is dict and set(binding)==BINDING_KEYS,"EXACT_ORIGINAL_ONLY_RECOVERY_BINDING")
 for name in DIGESTS:digest_value(binding[name],"EXACT_RECOVERY_BINDING_DIGEST")
 old=held.validate_binding(binding["original_binding"])
 need(binding["operation_id"]!=old["operation_id"] and binding["manifest_sha256"]!=old["manifest_sha256"]
      and binding["controller_sha256"]!=old["controller_sha256"],"NEW_DISTINCT_RECOVERY_SCOPE_REQUIRED")
 original,current=binding["original_holder"],binding["recovery_holder"]
 process(original,ORIGINAL_HOLDER_KEYS);process(current,RECOVERY_HOLDER_KEYS)
 need(original["positively_ceased"]is True and original["source_sha256"]==old["holder_sha256"],
      "ORIGINAL_HOLDER_POSITIVE_CESSATION_REQUIRED")
 digest_value(original["cessation_proof_sha256"],"ORIGINAL_HOLDER_CESSATION_PROOF_REQUIRED")
 need(current["same_release_flock_held"]is True and current["source_sha256"]==binding["signer_sha256"],
      "NEW_HOLDER_SAME_EXCLUSIVE_RELEASE_FLOCK_REQUIRED")
 digest_value(current["flock_proof_sha256"],"CURRENT_EXCLUSIVE_FLOCK_PROOF_REQUIRED")
 need((current["pid"],current["start_ticks"])!=(original["pid"],original["start_ticks"]),
      "ORIGINAL_HOLDER_CANNOT_RESIGN_OR_RENEW")
 carrier=binding["original_carrier"]
 need(type(carrier)is dict and set(carrier)==CARRIER_KEYS,"EXACT_ORIGINAL_TERMINAL_CARRIER_PROOF")
 for name in ("release_id","environment_id","attempt_id","restored_revision"):
  need(type(carrier[name])is int and carrier[name]>0,"EXACT_ORIGINAL_CARRIER_IDENTIFIERS")
 need(type(carrier["definition_id"])is int and carrier["definition_id"]==950
      and carrier["terminal_state"]in {"succeeded","failed","canceled","partiallySucceeded","rejected"}
      and carrier["business_fields_exact"]is True,"ORIGINAL_TERMINAL_AND_FULL_RESTORATION_REQUIRED")
 for name in ("terminal_receipt_sha256","definition_restoration_sha256"):
  digest_value(carrier[name],"ORIGINAL_CARRIER_RECEIPT_DIGEST_REQUIRED")
 instant(binding["holder_lease_expires_at"])
 return binding


def validate_observation(binding,value,*,now):
 validate_binding(binding)
 need(type(value)is dict and set(value)==OBSERVATION_KEYS,"EXACT_RECOVERY_CONTINUITY_OBSERVATION")
 held.validate_observation(binding["original_binding"],value["held_observation"],now=now)
 for name in OBSERVATION_KEYS-{"held_observation","original_barriers_continuously_retained",
                              "original_apply_resumed","barriers_recreated"}:
  need(equal(value[name],binding[name]),"ORIGINAL_RECOVERY_CONTEXT_OR_HOLDER_CHANGED")
 need(value["original_barriers_continuously_retained"]is True
      and value["original_apply_resumed"]is False and value["barriers_recreated"]is False,
      "ORIGINAL_BARRIERS_MUST_REMAIN_CONTINUOUSLY_HELD")
 return value


def make_receipt(binding,first,final,key,*,now):
 validate_observation(binding,first,now=now);validate_observation(binding,final,now=now)
 def stable(value):
  result=json.loads(canonical(value));result["held_observation"].pop("captured_at");return result
 need(equal(stable(first),stable(final)) and instant(first["held_observation"]["captured_at"])<=instant(final["held_observation"]["captured_at"]),
      "RECOVERY_HOLD_CHANGED_BETWEEN_OBSERVATIONS")
 need(type(key)is bytes and 32<=len(key.strip())<=4096,"EXISTING_RECOVERY_CONTROL_KEY_REQUIRED")
 observed=instant(final["held_observation"]["captured_at"])
 need(instant(binding["holder_lease_expires_at"])>=observed+OPERATION_SECONDS+RESERVE_SECONDS,
      "RECOVERY_HOLDER_LEASE_TOO_SHORT")
 payload={"schema_version":1,"purpose":PURPOSE,"site":"erp.abit.ai","definition_id":950,
  "binding":binding,"action":ACTION,"observed_at":utc(observed),
  "admission_expires_at":utc(observed+ADMISSION_SECONDS),"operation_expires_at":utc(observed+OPERATION_SECONDS),
  "current_observation_sha256":sha(canonical(final)),"continuous_original_hold_required":True,
  "original_apply_authority":False,"new_business_authority":False,"gate_open_authority":False}
 envelope={"payload":payload,"hmac_sha256":hmac.new(key.strip(),canonical(payload),hashlib.sha256).hexdigest()}
 raw=canonical(envelope);need(len(raw)<=MAX_ENVELOPE,"RECOVERY_RECEIPT_SIZE_BOUND")
 return base64.b64encode(raw).decode()


def verify(encoded,key,binding,*,now,admission=None):
 validate_binding(binding)
 need(type(encoded)is str and 0<len(encoded)<=MAX_ENVELOPE*4//3+4
      and type(key)is bytes and 32<=len(key.strip())<=4096,"BOUNDED_RECOVERY_HMAC_REQUIRED")
 try:
  raw=base64.b64decode(encoded,validate=True);need(len(raw)<=MAX_ENVELOPE,"RECOVERY_RECEIPT_SIZE_BOUND")
  def invalid(value):raise Held("NONFINITE_RECOVERY_RECEIPT")
  envelope=json.loads(raw,object_pairs_hook=unique,parse_constant=invalid)
 except (ValueError,TypeError)as error:
  if isinstance(error,Held):raise
  raise Held("INVALID_RECOVERY_RECEIPT_ENCODING")from None
 need(type(envelope)is dict and set(envelope)=={"payload","hmac_sha256"}
      and type(envelope["payload"])is dict,"EXACT_RECOVERY_ENVELOPE")
 digest_value(envelope["hmac_sha256"],"EXACT_RECOVERY_SIGNATURE")
 payload=envelope["payload"]
 need(hmac.compare_digest(hmac.new(key.strip(),canonical(payload),hashlib.sha256).hexdigest(),envelope["hmac_sha256"]),
      "INVALID_RECOVERY_SIGNATURE")
 keys={"schema_version","purpose","site","definition_id","binding","action","observed_at",
 "admission_expires_at","operation_expires_at","current_observation_sha256","continuous_original_hold_required",
 "original_apply_authority","new_business_authority","gate_open_authority"}
 need(set(payload)==keys and type(payload["schema_version"])is int and payload["schema_version"]==1
      and type(payload["definition_id"])is int and payload["definition_id"]==950
      and payload["purpose"]==PURPOSE and payload["action"]==ACTION and payload["site"]=="erp.abit.ai"
      and payload["continuous_original_hold_required"]is True and payload["original_apply_authority"]is False
      and payload["new_business_authority"]is False and payload["gate_open_authority"]is False
      and equal(payload["binding"],binding),"EXACT_ORIGINAL_RESTORE_ONLY_SCOPE")
 digest_value(payload["current_observation_sha256"],"EXACT_CURRENT_RECOVERY_OBSERVATION")
 observed=instant(payload["observed_at"]);initial=instant(payload["admission_expires_at"]);expires=instant(payload["operation_expires_at"])
 need(initial-observed==ADMISSION_SECONDS and expires-observed==OPERATION_SECONDS
      and instant(binding["holder_lease_expires_at"])>=expires+RESERVE_SECONDS and observed<=now<expires,
      "RECOVERY_OPERATION_WINDOW_EXPIRED_OR_FUTURE")
 stable={"receipt_sha256":sha(raw),"operation_id":binding["operation_id"],
  "original_operation_id":binding["original_binding"]["operation_id"],
  "admission_expires_at":payload["admission_expires_at"],"operation_expires_at":payload["operation_expires_at"]}
 if admission is None:
  need(now<initial,"RECOVERY_INITIAL_ADMISSION_EXPIRED")
  admitted={**stable,"admitted_at":utc(now),"body_deadline_at":utc(min(now+BODY_SECONDS,expires))}
 else:
  need(type(admission)is dict and set(admission)==set(stable)|{"admitted_at","body_deadline_at"}
       and equal({name:admission[name]for name in stable},stable),"EXACT_ONE_USE_RECOVERY_ADMISSION")
  admitted_at=instant(admission["admitted_at"])
  need(observed<=admitted_at<initial and admitted_at<=now and instant(admission["body_deadline_at"])==min(admitted_at+BODY_SECONDS,expires)
       and now<instant(admission["body_deadline_at"]),"RECOVERY_BODY_DEADLINE_EXPIRED_OR_CHANGED")
  admitted=admission
 return {"admission":admitted,"payload_sha256":sha(canonical(payload)),
  "original_operation_id":binding["original_binding"]["operation_id"],"action":ACTION,
  "original_barriers_must_remain_held":True,"original_apply_authority":False,"gate_open_authority":False}
