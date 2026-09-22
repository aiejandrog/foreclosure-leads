import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from document_agents import check_subscription, run_agent, subscription_env, validate_findings

HASH = "a" * 64
PAGE = {"document_hash": HASH, "page": 1, "text": "The judgment total is $123."}
FINDING = {"document_hash": HASH, "page": 1, "subject": "borrower", "claim": "Total $123",
           "excerpt": "$123", "kind": "court_finding", "status": "verified"}


def response(value, code=0):
    return SimpleNamespace(stdout=json.dumps(value), stderr="", returncode=code)


def auth():
    return response({"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty", "subscriptionType": "max"})


class AgentTests(unittest.TestCase):
    def test_no_api_auth_fallback(self):
        self.assertNotIn("ANTHROPIC_API_KEY", subscription_env({"ANTHROPIC_API_KEY": "secret"}))
        runner = Mock(return_value=response({"loggedIn": True, "authMethod": "apiKey"}))
        self.assertFalse(check_subscription(runner=runner)["ready"])

    def test_reader_cannot_self_verify(self):
        result = validate_findings({"findings": [FINDING], "unresolved": []}, [PAGE])
        self.assertEqual(result["findings"][0]["status"], "reported")

    def test_missing_page_and_invented_excerpt_rejected(self):
        for changes in ({"page": 2}, {"document_hash": "b" * 64}, {"excerpt": "$999"}):
            with self.assertRaises(ValueError):
                validate_findings({"findings": [{**FINDING, **changes}], "unresolved": []}, [PAGE], "verifier", [FINDING])

    def test_verifier_requires_candidate(self):
        payload = {"findings": [FINDING], "unresolved": []}
        self.assertEqual(validate_findings(payload, [PAGE], "verifier")["findings"][0]["status"], "reported")
        self.assertEqual(validate_findings(payload, [PAGE], "verifier", [FINDING])["findings"][0]["status"], "verified")

    def test_conditional_death_never_verified(self):
        page = {**PAGE, "text": "John, if deceased, unknown heirs."}
        finding = {**FINDING, "kind": "death", "excerpt": "if deceased"}
        result = validate_findings({"findings": [finding], "unresolved": []}, [page], "verifier", [finding])
        self.assertEqual(result["findings"][0]["status"], "unresolved")

    def test_tool_free_structured_subscription_run(self):
        runner = Mock(side_effect=[auth(), response({"structured_output": {"findings": [FINDING], "unresolved": []}})])
        result = run_agent("reader", [PAGE], runner=runner)
        self.assertEqual(result["status"], "complete")
        command = runner.call_args.args[0]
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertIn("--strict-mcp-config", command)
        self.assertNotIn("--bare", command)

    def test_usage_and_timeout_checkpoint(self):
        runner = Mock(side_effect=[auth(), response({"is_error": True, "result": "You've hit your limit"})])
        self.assertEqual(run_agent("reader", [PAGE], runner=runner)["status"], "usage_exhausted")
        runner = Mock(side_effect=[auth(), subprocess.TimeoutExpired("claude", 1)])
        self.assertTrue(run_agent("reader", [PAGE], runner=runner)["resumable"])

    def test_blank_page_does_not_complete(self):
        runner = Mock()
        self.assertEqual(run_agent("reader", [{**PAGE, "text": ""}], runner=runner)["status"], "needs_visual_review")
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
