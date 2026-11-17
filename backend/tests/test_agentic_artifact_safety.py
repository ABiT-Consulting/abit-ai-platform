"""Inspect actual SVG bytes, including decoded attributes, before publication."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    'artifact_safety', Path(__file__).resolve().parents[1] / 'open_webui/utils/agentic_artifacts.py'
)
artifacts = importlib.util.module_from_spec(spec); spec.loader.exec_module(artifacts)


class SVGArtifactSafetyTests(unittest.TestCase):
    def check_bytes(self, value):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); file = root/'diagram.svg'
            file.write_bytes(value); file.chmod(0o640)
            return artifacts.local_bytes(root, 'artifact://diagram.svg')[1]

    def test_plain_actual_svg_still_publishes_exact_bytes(self):
        data = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10" fill="#fff"/><text x="1" y="3">SRS</text></svg>'
        self.assertEqual(self.check_bytes(data), data)

    def test_xml_entities_cannot_hide_external_css_reference(self):
        for attribute in (b'style="fill:u&#114;l(https://example.invalid/a)"',
                          b'fill="u&#x72;l(https://example.invalid/a)"',
                          b'style="fill:\\75rl(https://example.invalid/a)"'):
            with self.subTest(attribute=attribute), self.assertRaisesRegex(ValueError, '(?i)external'):
                self.check_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"><rect '+attribute+b'/></svg>')

    def test_namespaced_event_and_stylesheet_processing_instructions_denied(self):
        for data in (b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:x="urn:fixture" x:onload="alert(1)"/>',
                     b'<?xml-stylesheet href="https://example.invalid/style.css"?><svg xmlns="http://www.w3.org/2000/svg"/>'):
            with self.subTest(data=data), self.assertRaisesRegex(ValueError, '(?i)external'):
                self.check_bytes(data)
