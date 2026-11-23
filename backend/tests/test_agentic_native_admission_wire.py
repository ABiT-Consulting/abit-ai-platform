"""Actual local Unix/HMAC framing, controlled root identity; no host services."""
from __future__ import annotations
import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import test_agentic_native_admission as core_tests
import test_agentic_native_issuer as root_tests


class WireCases(core_tests.CoreAdmissionCases):
    async def open_wire(self, *, changed_peer=False):
        path=Path(self.temp.name)/"native.sock";key=path.with_suffix(".key")
        key.write_bytes(b"k"*32);key.chmod(0o600)
        server=object.__new__(self.root.NativeBrokerServer)
        server.path=path;server.client_uid=os.getuid();server.socket_gid=os.getgid()
        server.key=b"k"*32;server.capacity=asyncio.Semaphore(4);server.nonces={};server.accepted=set();server.closing=False
        self.admission_calls=0;self.peer_checks=0;outer=self
        class Issuer:
            def admit(self,payload,grant,peer):
                outer.ac.validate_admission_grant(grant,payload,int(time.time()))
                outer.admission_calls+=1
                return outer.ac.observation(payload["attestation"])
            def observe(self,payload):return outer.ac.observation(payload["attestation"])
        native=object.__new__(self.root.RootNativeService);native.issuer=Issuer();server.native=native
        service=self.load("agentic_native_service")
        registration=self.service_registration(service);proof=registration["core_peer"]
        def peer(pid,uid,gid):
            self.peer_checks+=1
            result=service.process_proof(proof,pid,uid,gid)
            if changed_peer and self.peer_checks>1:result["start_ticks"]+=1
            return result
        server.verify_peer=peer
        actual=await asyncio.start_unix_server(server._handle,str(path))
        self.addAsyncCleanup(actual.wait_closed);self.addCleanup(actual.close)
        self.wire_server=server
        a=root_tests.attestation()
        async def auth(op,payload):
            if op=="admit":return root_tests.grant(payload["attestation"])
            return {"authorized":True,"mode":op,"request_payload_sha256":self.c.digest(payload),
                    "attestation_sha256":self.c.digest(payload["attestation"])}
        client=self.ad.NativeAdmissionClient.create(str(path),str(key),auth)
        client.server_uid=os.getuid()  # fixture root identity; kernel credentials are real.
        return client,a

    async def test_registered_wire_checks_kernel_peer_twice_before_admission(self):
        client,a=await self.open_wire()
        result=await client.request("admit",{"attestation":a})
        self.assertEqual(result["state"],"uncertain")
        self.assertEqual(self.admission_calls,1);self.assertEqual(self.peer_checks,2)
        self.assertEqual(len(self.wire_server.accepted),0)

    async def test_replaced_peer_after_challenge_denies_admission(self):
        client,a=await self.open_wire(changed_peer=True)
        with self.assertRaises(ValueError):await client.request("admit",{"attestation":a})
        self.assertEqual(self.admission_calls,0)

    async def test_original_admission_read_issues_no_root_authority(self):
        client,a=await self.open_wire()
        result=await client.request("observe_admission",{"attestation":a})
        self.assertEqual(result["state"],"uncertain");self.assertEqual(self.admission_calls,0)

    async def test_wrong_hmac_never_reaches_issuer(self):
        client,a=await self.open_wire();client.key=b"x"*32
        with self.assertRaises((ValueError,asyncio.IncompleteReadError)):
            await client.request("admit",{"attestation":a})
        self.assertEqual(self.admission_calls,0)

    async def test_observation_attestation_cannot_be_substituted_after_challenge(self):
        client,a=await self.open_wire()
        async def bad(op,payload):
            return {"authorized":True,"mode":op,"request_payload_sha256":self.c.digest(payload),
                    "attestation_sha256":"0"*64}
        client.authorize=bad
        with self.assertRaises(ValueError):await client.request("observe_admission",{"attestation":a})
        self.assertEqual(self.admission_calls,0)

    def test_default_off_and_unknown_mode_never_construct_client(self):
        with patch.object(self.ad.NativeAdmissionClient,"create") as create:
            self.assertIsNone(self.n.configured_runtime({},project="PROJ-0202",project_gate=self.gate,db_path=self.path))
            for config in ({"enabled":True,"admission_mode":"unknown"},
                           {"enabled":True,"admission_mode":self.ac.MODE,"projects":["PROJ-0196"],
                            "socket_path":"/irrelevant","key_file":"/irrelevant"}):
                with self.assertRaises(ValueError):
                    self.n.configured_runtime({"agentic_native_verification":config},
                        project="PROJ-0202",project_gate=self.gate,db_path=self.path)
            create.assert_not_called()

    def test_registration_requires_exact_new_module_set_and_opt_in(self):
        service=self.load("agentic_native_service");reg=self.service_registration(service)
        legacy=service.parse_registration(json.dumps(reg));self.assertEqual(legacy["schema_version"],1)
        reg.update(schema_version=2,issuer={"mode":self.ac.MODE,"subject_sha256":"8"*64,
            "control_sha256":{n:"9"*64 for n in root_tests.issuer.MODULES}})
        reg["module_sha256"]={name:"a"*64 for name in service.ISSUER_MODULE_NAMES}
        self.assertEqual(service.parse_registration(json.dumps(reg)),reg)
        for mutation in (lambda r:r["module_sha256"].pop("agentic_native_issuer.py"),
                         lambda r:r["issuer"].update(mode="arbitrary"),
                         lambda r:r.update(enabled=False)):
            wrong=deepcopy(reg);mutation(wrong)
            with self.assertRaises(ValueError):service.parse_registration(json.dumps(wrong))


def suite():
    return unittest.TestSuite(WireCases(name) for name in WireCases.__dict__ if name.startswith("test_"))


def load_tests(loader, tests, pattern):
    """Expose only this module's cases to standard unittest discovery."""
    return suite()


if __name__=="__main__":
    result=unittest.TextTestRunner(verbosity=2).run(suite());raise SystemExit(not result.wasSuccessful())
