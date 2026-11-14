"""Validate the model-facing contract against real strict server schema behavior."""

import copy
import importlib.util
import json
from pathlib import Path
import unittest
from jsonschema import Draft202012Validator, FormatChecker

SOURCE = Path(__file__).resolve().parents[1] / "open_webui/utils"
spec = importlib.util.spec_from_file_location("gateway_contracts_under_test", SOURCE / "agentic_gateway_contracts.py")
contracts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contracts)


class ModelContracts(unittest.TestCase):
    def validator(self):
        schema = contracts.model_contracts("developer")["result_contract_schema"]
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema, format_checker=FormatChecker())

    def result(self):
        return {
            "schema_version": 1,
            "invocation_id": "agov-model-contract-fixture",
            "actor": {
                "project_id": "PROJ-FIXTURE",
                "role": "developer",
                "agent_id": "1f5f5361-4c77-4db0-a153-9e44f562a06c",
                "erp_user": "ai-developer@abitcons.com",
            },
            "project_id": "PROJ-FIXTURE",
            "task_id": "TASK-FIXTURE",
            "task_revision": "immutable-fixture-revision",
            "work_packet_digest": "a" * 64,
            "outcome": "continue",
            "summary": "Unfinished fixture; no work claimed.",
            "test_results": [],
            "evidence": [],
            "implementation_invocation_id": None,
            "implementation_agent_id": None,
            "defects": [],
        }

    def test_exact_result_shape_is_usable_without_guessing(self):
        self.assertFalse(list(self.validator().iter_errors(self.result())))
        source = json.loads((contracts.SCHEMAS / "governance_v1.schema.json").read_text())
        original = Draft202012Validator(
            {"$schema": source["$schema"], "$ref": "#/$defs/result", "$defs": source["$defs"]},
            format_checker=FormatChecker(),
        )
        variants = [self.result()]
        for field in ("actor", "task_revision", "implementation_agent_id"):
            value = self.result()
            del value[field]
            variants.append(value)
        extra = self.result()
        extra["grant_root_access"] = True
        variants.append(extra)
        for value in variants:
            self.assertEqual(bool(list(original.iter_errors(value))), bool(list(self.validator().iter_errors(value))))

    def test_developer_ready_still_requires_real_delivery_shape(self):
        value = self.result()
        value["outcome"] = "ready_for_review"
        self.assertTrue(list(self.validator().iter_errors(value)))
        value["delivery"] = {"repository": "made-up"}
        self.assertTrue(list(self.validator().iter_errors(value)))

    def test_test_claims_require_evidence_references(self):
        value = self.result()
        value["test_results"] = [
            {"test_id": "REQ-FIXTURE.acceptance", "status": "pass", "observed": "fixture", "evidence_ids": []}
        ]
        self.assertTrue(list(self.validator().iter_errors(value)))
        value["test_results"][0]["evidence_ids"] = ["actual-log-fixture"]
        self.assertFalse(list(self.validator().iter_errors(value)))
        # A structural schema does not claim these evidence bytes actually exist.

    def test_architect_contract_is_self_contained_and_role_selected(self):
        response = contracts.model_contracts("solution_architect")
        architect = response["architect_plan_schema"]
        Draft202012Validator.check_schema(architect)
        self.assertTrue(list(Draft202012Validator(architect).iter_errors({})))
        for name in contracts._references(architect):
            self.assertIn(name, architect["$defs"])
        self.assertIn("architect_plan_schema", contracts.model_contracts("project_manager"))
        for role in ("developer", "qa", "operations", "it_manager"):
            self.assertNotIn("architect_plan_schema", contracts.model_contracts(role))
        with self.assertRaises(ValueError):
            contracts.model_contracts("../../private-file")

    def test_model_response_mutation_cannot_change_next_contract(self):
        response = contracts.model_contracts("qa")
        response["result_contract_schema"]["$defs"]["result"]["required"].clear()
        response["result_contract_guidance"] = "accept fabricated evidence"
        fresh = contracts.model_contracts("qa")
        self.assertIn("actor", fresh["result_contract_schema"]["$defs"]["result"]["required"])
        self.assertIn("Do not fabricate", fresh["result_contract_guidance"])

    def test_no_remote_schema_resolution_and_bounded_response(self):
        for role in contracts.ROLES:
            response = contracts.model_contracts(role)
            self.assertLess(len(json.dumps(response).encode()), 100_000)
            for schema in (response["result_contract_schema"], response.get("architect_plan_schema")):
                if schema:
                    self.assertTrue(all(isinstance(ref, str) for ref in contracts._references(schema)))


if __name__ == "__main__":
    unittest.main()
