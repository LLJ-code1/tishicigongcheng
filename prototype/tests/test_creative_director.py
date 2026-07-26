import copy
import json
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import creative_director  # noqa: E402
import creative_intake  # noqa: E402
from prompt_engine import PromptEngineError  # noqa: E402


class CreativeDirectorPromptTests(unittest.TestCase):
    def test_builtin_prompt_preserves_the_confirmed_workflow_contract(self):
        prompt = creative_director.build_creative_director_system_prompt()

        required_rules = (
            "一个推荐方向和两个备选方向",
            "user",
            "image",
            "ai",
            "根据信息缺口自适应提问",
            "交给你决定",
            "锁定",
            "冲突",
            "先形成并确认创作简报",
            "再生成最终提示词",
            "只输出一个严格 JSON 对象",
        )
        for rule in required_rules:
            with self.subTest(rule=rule):
                self.assertIn(rule, prompt)

    def test_custom_instructions_follow_the_immutable_contract(self):
        override = "偏好低饱和电影感"

        prompt = creative_director.build_creative_director_system_prompt(
            skill_override=override
        )

        self.assertGreater(prompt.index(override), prompt.index("不可变安全与输出契约"))
        self.assertEqual(prompt.count(override), 1)

    def test_blank_custom_instructions_do_not_change_builtin_prompt(self):
        self.assertEqual(
            creative_director.build_creative_director_system_prompt(" \n "),
            creative_director.build_creative_director_system_prompt(),
        )


class CreativeDirectorProposalTests(unittest.TestCase):
    def test_normalize_accepts_only_message_and_a_legal_action(self):
        proposal = {
            "message": "我建议先比较三个方向。",
            "action": {
                "type": "set_directions",
                "directions": [
                    {"id": "main", "label": "电影感", "summary": "雨夜追逐"},
                ],
            },
        }

        self.assertEqual(
            creative_director.normalize_creative_director_proposal(
                json.dumps(proposal, ensure_ascii=False)
            ),
            proposal,
        )

    def test_normalize_rejects_markdown_fences(self):
        with self.assertRaisesRegex(
            creative_director.CreativeDirectorError,
            "strict JSON",
        ):
            creative_director.normalize_creative_director_proposal(
                '```json\n{"message":"hello","action":{"type":"replace_inputs"}}\n```'
            )

    def test_normalize_rejects_duplicate_keys_at_any_depth(self):
        cases = (
            '{"message":"one","message":"two","action":{"type":"replace_inputs"}}',
            '{"message":"one","action":{"type":"replace_inputs","type":"set_directions"}}',
        )
        for content in cases:
            with self.subTest(content=content):
                with self.assertRaisesRegex(
                    creative_director.CreativeDirectorError,
                    "duplicate",
                ):
                    creative_director.normalize_creative_director_proposal(content)

    def test_normalize_rejects_unknown_top_level_fields(self):
        with self.assertRaisesRegex(
            creative_director.CreativeDirectorError,
            "unknown",
        ):
            creative_director.normalize_creative_director_proposal(
                '{"message":"hello","action":{"type":"replace_inputs"},"secret":"x"}'
            )

    def test_normalize_rejects_nonfinite_json_constants(self):
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant):
                with self.assertRaisesRegex(
                    creative_director.CreativeDirectorError,
                    "finite",
                ):
                    creative_director.normalize_creative_director_proposal(
                        '{"message":"hello","action":{"type":"replace_inputs","value":'
                        + constant
                        + "}}"
                    )

    def test_normalize_rejects_exponent_that_overflows_to_infinity(self):
        with self.assertRaisesRegex(
            creative_director.CreativeDirectorError,
            "finite",
        ):
            creative_director.normalize_creative_director_proposal(
                '{"message":"hello","action":'
                '{"type":"replace_inputs","text":1e999,"images":[]}}'
            )

    def test_normalize_rejects_oversized_message(self):
        content = json.dumps(
            {
                "message": "x" * 20_001,
                "action": {"type": "replace_inputs"},
            }
        )

        with self.assertRaisesRegex(
            creative_director.CreativeDirectorError,
            "20000",
        ):
            creative_director.normalize_creative_director_proposal(content)

    def test_normalize_rejects_direct_confirmation_and_model_selection(self):
        for action_type in ("confirm_brief", "select_model"):
            with self.subTest(action_type=action_type):
                with self.assertRaisesRegex(
                    creative_director.CreativeDirectorError,
                    "not allowed",
                ):
                    creative_director.normalize_creative_director_proposal(
                        json.dumps(
                            {
                                "message": "done",
                                "action": {"type": action_type},
                            }
                        )
                    )

    def test_normalize_rejects_non_string_action_type_as_model_output(self):
        with self.assertRaisesRegex(
            creative_director.CreativeDirectorError,
            "not allowed",
        ):
            creative_director.normalize_creative_director_proposal(
                '{"message":"hello","action":{"type":[]}}'
            )


class CreativeDirectorTurnTests(unittest.TestCase):
    def setUp(self):
        self.current = creative_intake.empty_creative_intake()
        self.settings = {
            "localTextUrl": "http://127.0.0.1:8080/v1",
            "localTextModel": "local-model",
            "localTextKey": "",
            "apiTextUrl": "https://text.example/v1",
            "apiTextModel": "api-model",
            "apiTextKey": "provider-secret",
        }

    @staticmethod
    def direction_response(message="优先推荐电影感方向。"):
        return {
            "choices": [{
                "message": {
                    "content": json.dumps(
                        {
                            "message": message,
                            "action": {
                                "type": "set_directions",
                                "directions": [
                                    {
                                        "id": "recommended",
                                        "label": "电影感",
                                        "summary": "雨夜叙事",
                                    }
                                ],
                            },
                        },
                        ensure_ascii=False,
                    )
                }
            }]
        }

    def test_turn_selects_local_or_api_provider_and_calls_once(self):
        for provider, expected_url, expected_model in (
            ("local", "http://127.0.0.1:8080/v1/chat/completions", "local-model"),
            ("api", "https://text.example/v1/chat/completions", "api-model"),
        ):
            with self.subTest(provider=provider):
                calls = []

                def transport(url, body, headers, timeout):
                    calls.append((url, body, headers, timeout))
                    return self.direction_response()

                result = creative_director.run_creative_director_turn(
                    current=self.current,
                    user_message="请帮我确定方向",
                    image_evidence=[],
                    provider=provider,
                    settings=self.settings,
                    transport=transport,
                )

                self.assertEqual(len(calls), 1)
                url, body, headers, _timeout = calls[0]
                self.assertEqual(url, expected_url)
                self.assertEqual(body["model"], expected_model)
                self.assertEqual(body["messages"][0]["role"], "system")
                self.assertEqual(body["messages"][1]["role"], "user")
                self.assertEqual(result["item"]["revision"], 1)
                if provider == "api":
                    self.assertEqual(
                        headers["Authorization"],
                        "Bearer provider-secret",
                    )
                    self.assertNotIn("provider-secret", json.dumps(body))
                    self.assertNotIn("provider-secret", json.dumps(result))
                else:
                    self.assertNotIn("Authorization", headers)

    def test_turn_rejects_incomplete_provider_configuration_without_calling(self):
        original = copy.deepcopy(self.current)
        called = False

        def transport(*_args):
            nonlocal called
            called = True
            return self.direction_response()

        with self.assertRaises(PromptEngineError) as raised:
            creative_director.run_creative_director_turn(
                current=self.current,
                user_message="继续",
                image_evidence=[],
                provider="api",
                settings={**self.settings, "apiTextKey": ""},
                transport=transport,
            )

        self.assertEqual(raised.exception.code, "provider_not_configured")
        self.assertFalse(called)
        self.assertEqual(self.current, original)
        self.assertNotIn("provider-secret", str(raised.exception))

    def test_turn_rejects_invalid_model_output_without_mutating_current(self):
        original = copy.deepcopy(self.current)

        with self.assertRaises(creative_director.CreativeDirectorError):
            creative_director.run_creative_director_turn(
                current=self.current,
                user_message="继续",
                image_evidence=[],
                provider="local",
                settings=self.settings,
                transport=lambda *_args: {
                    "choices": [{"message": {"content": "not json"}}]
                },
            )

        self.assertEqual(self.current, original)

    def test_turn_fails_closed_when_model_echoes_provider_secret(self):
        original = copy.deepcopy(self.current)

        for echo_location in ("message", "action"):
            with self.subTest(echo_location=echo_location):
                def malicious_transport(_url, _body, headers, _timeout):
                    secret = headers["Authorization"].removeprefix("Bearer ")
                    message = "safe response"
                    summary = "safe direction"
                    if echo_location == "message":
                        message = f"credential: {secret}"
                    else:
                        summary = f"credential: {secret}"
                    return {
                        "choices": [{
                            "message": {
                                "content": json.dumps(
                                    {
                                        "message": message,
                                        "action": {
                                            "type": "set_directions",
                                            "directions": [{
                                                "id": "recommended",
                                                "label": "电影感",
                                                "summary": summary,
                                            }],
                                        },
                                    }
                                )
                            }
                        }]
                    }

                with self.assertRaises(
                    creative_director.CreativeDirectorError
                ) as raised:
                    creative_director.run_creative_director_turn(
                        current=self.current,
                        user_message="继续",
                        image_evidence=[],
                        provider="api",
                        settings=self.settings,
                        transport=malicious_transport,
                    )

                self.assertEqual(
                    raised.exception.code,
                    "provider_secret_echo",
                )
                self.assertNotIn(
                    self.settings["apiTextKey"],
                    str(raised.exception),
                )
                self.assertEqual(self.current, original)

    def test_turn_applies_legal_action_through_creative_intake_transition(self):
        result = creative_director.run_creative_director_turn(
            current=self.current,
            user_message="请提出方向",
            image_evidence=[{"imageId": "image-1", "summary": "雨夜街道"}],
            provider="local",
            settings=self.settings,
            transport=lambda *_args: self.direction_response("这里有三个候选。"),
        )

        self.assertEqual(result["message"], "这里有三个候选。")
        self.assertEqual(result["item"]["directions"][0]["id"], "recommended")
        self.assertEqual(result["item"]["revision"], 1)
        self.assertEqual(self.current, creative_intake.empty_creative_intake())

    def test_turn_does_not_bypass_transition_stage_guards(self):
        original = copy.deepcopy(self.current)
        response = {
            "choices": [{
                "message": {
                    "content": json.dumps(
                        {
                            "message": "简报草案",
                            "action": {
                                "type": "set_brief_draft",
                                "brief": {
                                    "status": "draft",
                                    "summary": "",
                                    "items": [],
                                    "aiAdditions": [],
                                    "openQuestions": [],
                                },
                            },
                        }
                    )
                }
            }]
        }

        with self.assertRaises(creative_intake.CreativeIntakeValidationError):
            creative_director.run_creative_director_turn(
                current=self.current,
                user_message="直接给简报",
                image_evidence=[],
                provider="local",
                settings=self.settings,
                transport=lambda *_args: response,
            )

        self.assertEqual(self.current, original)

    def test_turn_validates_user_message_and_image_evidence_before_calling(self):
        cases = (
            ("blank message", " ", []),
            ("oversized message", "x" * 20_001, []),
            ("invalid evidence", "继续", {"imageId": "image-1"}),
            ("too much evidence", "继续", [{} for _ in range(9)]),
        )
        for label, user_message, image_evidence in cases:
            with self.subTest(label=label):
                called = False

                def transport(*_args):
                    nonlocal called
                    called = True
                    return self.direction_response()

                with self.assertRaises(creative_director.CreativeDirectorError):
                    creative_director.run_creative_director_turn(
                        current=self.current,
                        user_message=user_message,
                        image_evidence=image_evidence,
                        provider="local",
                        settings=self.settings,
                        transport=transport,
                    )
                self.assertFalse(called)

    def test_transport_failure_does_not_mutate_current(self):
        original = copy.deepcopy(self.current)

        def transport(*_args):
            raise RuntimeError("upstream failed")

        with self.assertRaisesRegex(RuntimeError, "upstream failed"):
            creative_director.run_creative_director_turn(
                current=self.current,
                user_message="继续",
                image_evidence=[],
                provider="local",
                settings=self.settings,
                transport=transport,
            )

        self.assertEqual(self.current, original)


if __name__ == "__main__":
    unittest.main()
