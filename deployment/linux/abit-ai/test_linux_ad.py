"""Focused security checks for the Linux AD adapter; no real login attempts."""
import importlib.util
from pathlib import Path
import stat
import subprocess
import sys
import types
import unittest
from unittest.mock import patch

if len(sys.argv)>1 and not sys.argv[1].startswith("-"):
    module_path=Path(sys.argv.pop(1))
else:
    module_path=Path(__file__).with_name("linux_ad.py")
    if not module_path.is_file():
        module_path=Path(__file__).resolve().parents[3]/"backend/open_webui/utils/linux_ad.py"
spec=importlib.util.spec_from_file_location("linux_ad",module_path)
ad=importlib.util.module_from_spec(spec)
spec.loader.exec_module(ad)

class LinuxAdTests(unittest.TestCase):
    def test_bad_input_never_starts_pam(self):
        with patch.object(ad.subprocess,"run") as run:
            for upn,password in [("","x"),("root","x"),("a@abitcons.com",""),("a@abitcons.com"," "),("a@abitcons.com","x\nsecond"),("a@abitcons.com","x\0y"),("a@abitcons.com","x"*4097)]:
                self.assertEqual(ad.validate_linux_ad_credentials(upn,password),(False,1326))
            run.assert_not_called()
    def test_missing_dedicated_policy_fails_closed(self):
        with patch.object(ad,"_trusted_file",return_value=False),patch.object(ad.subprocess,"run") as run:
            self.assertEqual(ad.validate_linux_ad_credentials("probe@abitcons.com","synthetic"),(False,-1))
            run.assert_not_called()
    def test_success_requires_both_authentication_and_account_check(self):
        with patch.object(ad,"_trusted_file",return_value=True),patch.object(ad.subprocess,"run",return_value=types.SimpleNamespace(returncode=0)) as run:
            self.assertEqual(ad.validate_linux_ad_credentials("probe@abitcons.com","synthetic"),(True,0))
            args,kwargs=run.call_args
            self.assertEqual(args[0],[str(ad.PAM_EXECUTABLE),"abit-ai","probe@abitcons.com","authenticate(PAM_SILENT|PAM_DISALLOW_NULL_AUTHTOK)","acct_mgmt"])
            self.assertNotIn("synthetic"," ".join(args[0]))
            self.assertEqual(kwargs["input"],"synthetic\n")
            self.assertFalse(kwargs.get("shell",False))
            self.assertEqual(kwargs["stdout"],subprocess.DEVNULL)
            self.assertEqual(kwargs["stderr"],subprocess.DEVNULL)
            self.assertLessEqual(kwargs["timeout"],6)
            self.assertEqual(set(kwargs["env"]),{"PATH","LANG","LC_ALL"})
    def test_authentication_or_account_failure_is_rejected(self):
        with patch.object(ad,"_trusted_file",return_value=True),patch.object(ad.subprocess,"run",return_value=types.SimpleNamespace(returncode=1)):
            self.assertEqual(ad.validate_linux_ad_credentials("probe@abitcons.com","synthetic"),(False,-2))
    def test_timeout_releases_concurrency_slot(self):
        with patch.object(ad,"_trusted_file",return_value=True),patch.object(ad.subprocess,"run",side_effect=subprocess.TimeoutExpired("pamtester",6)):
            self.assertEqual(ad.validate_linux_ad_credentials("probe@abitcons.com","synthetic"),(False,-1))
        with patch.object(ad,"_trusted_file",return_value=True),patch.object(ad.subprocess,"run",return_value=types.SimpleNamespace(returncode=0)):
            self.assertEqual(ad.validate_linux_ad_credentials("probe@abitcons.com","synthetic"),(True,0))
    def test_busy_adapter_does_not_spawn(self):
        slots=types.SimpleNamespace(acquire=lambda **kwargs:False)
        with patch.object(ad,"_PAM_SLOTS",slots),patch.object(ad,"_trusted_file",return_value=True),patch.object(ad.subprocess,"run") as run:
            self.assertEqual(ad.validate_linux_ad_credentials("probe@abitcons.com","synthetic"),(False,-1))
            run.assert_not_called()
    def test_policy_must_be_root_owned_regular_and_nonwritable(self):
        path=Path("/etc/pam.d/abit-ai")
        good_parent=types.SimpleNamespace(st_uid=0,st_mode=stat.S_IFDIR|0o755)
        for mode,uid,expected in [(stat.S_IFREG|0o644,0,True),(stat.S_IFREG|0o664,0,False),(stat.S_IFREG|0o646,0,False),(stat.S_IFREG|0o644,999,False),(stat.S_IFLNK|0o777,0,False)]:
            info=types.SimpleNamespace(st_uid=uid,st_mode=mode,st_size=100)
            with patch.object(Path,"lstat",return_value=info),patch.object(Path,"stat",return_value=good_parent):
                self.assertEqual(ad._trusted_file(path),expected)
    def test_writable_parent_rejected(self):
        info=types.SimpleNamespace(st_uid=0,st_mode=stat.S_IFREG|0o644,st_size=100)
        parent=types.SimpleNamespace(st_uid=0,st_mode=stat.S_IFDIR|0o777)
        with patch.object(Path,"lstat",return_value=info),patch.object(Path,"stat",return_value=parent):
            self.assertFalse(ad._trusted_file(Path("/etc/pam.d/abit-ai")))

if __name__=="__main__": unittest.main()
