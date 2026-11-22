from pathlib import Path
R=Path(__file__).resolve().parents[3]
p=R/'backend/tests/test_agentic_native_verification.py';s=p.read_text();assert 'test_root_standalone_bundle_loads_only_exact_bytes_without_core_imports' not in s
s+='''

    def service_registration(self, service):
        base=Path(self.n.__file__).parent
        pid=os.getpid()
        start=int(Path(f"/proc/{pid}/stat").read_text().rsplit(") ",1)[1].split()[19])
        return {"schema_version":1,"enabled":True,
                "module_sha256":{name:hashlib.sha256((base/name).read_bytes()).hexdigest() for name in service.MODULE_NAMES},
                "core_peer":{"pid":pid,"uid":os.getuid(),"gid":os.getgid(),"start_ticks":start,
                             "cmdline_sha256":hashlib.sha256(Path(f"/proc/{pid}/cmdline").read_bytes()).hexdigest(),
                             "boot_id":Path("/proc/sys/kernel/random/boot_id").read_text().strip()}}

    def test_root_registration_is_default_denied_and_has_no_arbitrary_resource_fields(self):
        service=self.load("agentic_native_service")
        registration=self.service_registration(service)
        self.assertEqual(service.parse_registration(json.dumps(registration)),registration)
        changes=[{"enabled":False},{"command":["arbitrary"]},{"module_sha256":dict.fromkeys(service.MODULE_NAMES,"PENDING_DENY")},
                 {"core_peer":{**registration["core_peer"],"uid":0}}]
        for change in changes:
            with self.subTest(change=tuple(change)):
                with self.assertRaises(ValueError):service.parse_registration(json.dumps({**registration,**change}))
        with self.assertRaisesRegex(ValueError,"DUPLICATE"):
            service.parse_registration('{"enabled":false,"enabled":true}')

    def test_exact_kernel_peer_proof_rejects_pid_generation_uid_command_and_boot_drift(self):
        service=self.load("agentic_native_service")
        peer=self.service_registration(service)["core_peer"]
        args=(peer["pid"],peer["uid"],peer["gid"])
        self.assertEqual(service.process_proof(peer,*args),peer)
        for key,value in (("pid",peer["pid"]+1),("uid",peer["uid"]+1),("gid",peer["gid"]+1),
                          ("start_ticks",peer["start_ticks"]+1),("cmdline_sha256","a"*64),("boot_id","a"*36)):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError,"CHANGED"):
                    service.process_proof({**peer,key:value},*args)

    def test_fixed_root_entrypoint_rejects_wrong_identity_location_or_arguments_before_reads(self):
        service=self.load("agentic_native_service")
        with patch.object(service,"source_bytes") as read:
            with self.assertRaisesRegex(ValueError,"FIXED_ROOT_ENTRYPOINT"):
                service.main()
            with patch.object(service.os,"geteuid",return_value=0),patch.object(service.sys,"argv",["fixed"]):
                with self.assertRaisesRegex(ValueError,"FIXED_ROOT_ENTRYPOINT"):service.main()
            with patch.object(service.os,"geteuid",return_value=0),patch.object(service,"__file__",str(service.SERVICE/"agentic_native_service.py")), \\
                 patch.object(service.sys,"argv",["fixed","--command=other"]):
                with self.assertRaisesRegex(ValueError,"FIXED_ROOT_ENTRYPOINT"):service.main()
            read.assert_not_called()

    def test_root_standalone_bundle_loads_only_exact_bytes_without_core_imports(self):
        import builtins,sys
        service=self.load("agentic_native_service")
        registration=self.service_registration(service)
        base=Path(self.n.__file__).parent
        reads=[];original_import=builtins.__import__
        def read(path,limit):
            self.assertEqual(Path(path).parent,service.SERVICE);self.assertIn(Path(path).name,service.MODULE_NAMES)
            reads.append(Path(path).name);return (base/Path(path).name).read_bytes()
        def guarded_import(name,*args,**kwargs):
            if name.startswith("open_webui"):raise AssertionError("Root bundle imported writable application")
            return original_import(name,*args,**kwargs)
        with patch.dict(sys.modules,{}),patch.object(service,"source_bytes",side_effect=read), \\
             patch.object(builtins,"__import__",side_effect=guarded_import):
            modules=service.load_modules(registration)
            self.assertEqual(reads,list(service.MODULE_NAMES))
            self.assertEqual(set(modules),{"agentic_native_contract","agentic_native_broker"})
            self.assertEqual(modules["agentic_native_broker"].CONTROL_HASHES,self.root.CONTROL_HASHES)
            self.assertEqual(modules["agentic_native_broker"].digest({"x":"unicode-Ω"}),self.c.digest({"x":"unicode-Ω"}))

    def test_root_bundle_hash_mismatch_rejects_before_any_module_execution(self):
        import sys
        service=self.load("agentic_native_service")
        registration=self.service_registration(service)
        registration["module_sha256"]["agentic_native_broker.py"]="f"*64
        base=Path(self.n.__file__).parent
        with patch.dict(sys.modules,{"agentic_native_contract":"unchanged-contract","agentic_native_broker":"unchanged-broker"}), \\
             patch.object(service,"source_bytes",side_effect=lambda path,limit:(base/Path(path).name).read_bytes()):
            with self.assertRaisesRegex(ValueError,"BUNDLE_BYTES_CHANGED"):service.load_modules(registration)
            self.assertEqual(sys.modules["agentic_native_contract"],"unchanged-contract")
            self.assertEqual(sys.modules["agentic_native_broker"],"unchanged-broker")

    async def test_actual_retained_wire_framing_interoperates_and_rejects_corrupted_mac(self):
        class Writer:
            def __init__(self):self.raw=b""
            def write(self,raw):self.raw+=raw
            async def drain(self):pass
        key=b"x"*32;body={"schema_version":1,"message":"unicode-Ω","nonce":"f"*48}
        for sender,receiver in ((self.root,self.wire),(self.wire,self.root)):
            writer=Writer();await sender._send(writer,body,key)
            reader=asyncio.StreamReader();reader.feed_data(writer.raw);reader.feed_eof()
            self.assertEqual(await receiver._read(reader,key),body)
            reader=asyncio.StreamReader();reader.feed_data(writer.raw);reader.feed_eof()
            with self.assertRaisesRegex(ValueError,"authentication"):
                await receiver._read(reader,b"z"*32)

    async def test_root_service_close_waits_for_accepted_launch_and_blocks_new_handlers(self):
        server=object.__new__(self.root.NativeBrokerServer)
        server.closing=False;server.server=types.SimpleNamespace(close=lambda:None,wait_closed=AsyncMock())
        server.path=Path(self.temp.name)/"absent-broker.sock"
        wait=asyncio.Event()
        accepted=asyncio.create_task(wait.wait());server.accepted={accepted}
        closing=asyncio.create_task(server.close())
        await asyncio.sleep(0)
        self.assertTrue(server.closing);self.assertFalse(closing.done())
        wait.set();await closing
        self.assertTrue(accepted.done())

    def test_maintenance_zero_epoch_is_valid_but_negative_or_boolean_epoch_is_rejected(self):
        proof={"lease_id":"a"*48,"kind":"governed","reference":"original-fixture","epoch":0}
        self.assertEqual(self.c.validate_maintenance(proof,"original-fixture"),proof)
        for value in (-1,True):
            with self.assertRaises(ValueError):self.c.validate_maintenance({**proof,"epoch":value},"original-fixture")
'''
p.write_text(s)
