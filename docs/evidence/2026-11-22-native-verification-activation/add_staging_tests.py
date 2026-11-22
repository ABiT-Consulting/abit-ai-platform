from pathlib import Path
p = Path(__file__).with_name('test_native_activation.py')
text = p.read_text()
addition = r'''
    def stager_fixture(self):
        stage = self.root / 'stage'
        stage.mkdir()
        bundle = self.root / 'var-lib-abit/native-verification/service'
        unit = self.root / 'etc-systemd/native.service'
        unit.parent.mkdir()
        payload = {bundle / 'one.py': (b'first reviewed module', 0o400),
                   bundle / 'registration.json': (b'{"enabled":false}', 0o400),
                   unit: (b'[Service]\nRestart=no\n', 0o644)}
        self.use(module=s, STAGE=stage, BUNDLE=bundle, UNIT=unit,
                 JOURNAL=stage / 'journal.json', operator=Mock(), inactive=Mock(),
                 default_off=Mock(), inputs=Mock(return_value=payload),
                 trusted_directory=Mock())
        self.stack.enter_context(patch.object(os, 'fchown', return_value=None))
        # The local test account cannot manufacture uid0. Root ownership remains
        # enforced in product owned_file and is separately checked by its AST/
        # source review; here only the real file/mode/content lifecycle is run.
        def fixture_owned(path, data, mode):
            info = path.lstat()
            self.assertTrue(path.is_file() and not path.is_symlink())
            self.assertEqual(info.st_nlink, 1)
            self.assertEqual(info.st_mode & 0o777, mode)
            self.assertEqual(path.read_bytes(), data)
        self.use(module=s, owned_file=fixture_owned)
        return stage, bundle, payload

    def test_preexisting_broker_prefix_is_not_adopted(self):
        stage, bundle, payload = self.stager_fixture()
        bundle.parent.mkdir(parents=True)
        preserved = bundle.parent / 'unknown'
        preserved.write_bytes(b'other owner')
        with self.assertRaisesRegex(RuntimeError, 'already exists'):
            s.apply()
        self.assertFalse(s.JOURNAL.exists())
        self.assertEqual(preserved.read_bytes(), b'other owner')

    def test_partial_local_broker_stage_reconciles_without_replacing_completed_inode(self):
        stage, bundle, payload = self.stager_fixture()
        actual = s.create_exact
        first = bundle / 'one.py'
        def fail_after_first(path, data, mode):
            actual(path, data, mode)
            if path == first:
                raise TimeoutError('fixture interrupted after durable file creation')
        with patch.object(s, 'create_exact', side_effect=fail_after_first):
            with self.assertRaises(TimeoutError):
                s.apply()
        inode = first.stat().st_ino
        self.assertEqual(json.loads(s.JOURNAL.read_text())['phase'], 'intent_saved')
        with patch('sys.stdout', new=io.StringIO()):
            s.apply()
        record = json.loads(s.JOURNAL.read_text())
        self.assertEqual(record['phase'], 'staged_disabled')
        self.assertFalse(record['service_started'])
        self.assertFalse(record['keys_created'])
        self.assertFalse(record['authority_issued'])
        self.assertEqual(first.stat().st_ino, inode)
        self.assertEqual(first.read_bytes(), payload[first][0])

    def test_partial_stage_unknown_bundle_file_holds_without_removal(self):
        stage, bundle, payload = self.stager_fixture()
        actual = s.create_exact
        first = bundle / 'one.py'
        def fail_after_first(path, data, mode):
            actual(path, data, mode)
            if path == first:
                raise TimeoutError('fixture pause')
        with patch.object(s, 'create_exact', side_effect=fail_after_first):
            with self.assertRaises(TimeoutError):
                s.apply()
        unknown = bundle / 'unknown.py'
        unknown.write_bytes(b'preserve')
        with self.assertRaisesRegex(RuntimeError, 'Unexpected broker bundle'):
            s.apply()
        self.assertEqual(unknown.read_bytes(), b'preserve')

    def test_active_broker_prevents_staging_before_intent(self):
        stage, bundle, payload = self.stager_fixture()
        self.use(module=s, inactive=Mock(side_effect=RuntimeError('already active')))
        with self.assertRaisesRegex(RuntimeError, 'already active'):
            s.apply()
        self.assertFalse(s.JOURNAL.exists())
        self.assertFalse(bundle.parent.exists())

    def test_actual_untrusted_ancestry_is_rejected_read_only(self):
        # This is ordinary metadata inspection, with no ownership/mode changes.
        with self.assertRaisesRegex(RuntimeError, 'exclusively administered'):
            s.trusted_directory(self.root)
'''
assert 'def stager_fixture(' not in text
text = text.replace("\n\nif __name__ == '__main__':", '\n' + addition + "\n\nif __name__ == '__main__':")
p.write_text(text)
