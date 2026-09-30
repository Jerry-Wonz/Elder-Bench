"""ChatClient 请求参数与响应元数据回归测试。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import httpx

EVAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL))

from model_client import ChatClient, ChatResult, SystemPromptChatClient  # noqa: E402
from run_eval import _make_subject  # noqa: E402
from runner import run_suite  # noqa: E402
from schema import EvalTask, save_tasks  # noqa: E402


class ModelClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_full_sampling_params_and_usage(self):
        captured: dict = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(200, json={
                "model": "deepseek-flash",
                "choices": [{
                    "finish_reason": "stop",
                    "message": {
                        "content": "reasoning\n</think>\nvisible",
                        "reasoning_content": "separate reasoning",
                    },
                }],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7},
            })

        client = ChatClient(
            "http://example.test/v1", model="deepseek-flash", temperature=0.0,
            max_tokens=6144, top_p=1.0, seed=20260910,
            presence_penalty=0.0, frequency_penalty=0.0, stop=["END"],
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            transport=httpx.MockTransport(handler),
        )
        result = await client.chat([{"role": "user", "content": "hi"}])

        self.assertEqual(captured["top_p"], 1.0)
        self.assertEqual(captured["seed"], 20260910)
        self.assertEqual(captured["presence_penalty"], 0.0)
        self.assertEqual(captured["frequency_penalty"], 0.0)
        self.assertEqual(captured["stop"], ["END"])
        self.assertEqual(captured["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(result.content, "visible")
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(result.prompt_tokens, 11)
        self.assertEqual(result.completion_tokens, 7)
        self.assertGreater(result.reasoning_chars, 0)

    async def test_runner_persists_generation_metadata(self):
        class Model:
            async def chat(self, messages, json_mode=False, extra=None):
                return ChatResult(
                    content="visible", finish_reason="stop", prompt_tokens=3,
                    completion_tokens=2, request={"temperature": 0.0, "max_tokens": 8})

        task = EvalTask(task_id="t1", dimension="medical", suite="health_ref",
                        source="test", lang="en", messages=[{"role": "user", "content": "hi"}],
                        reference={"gold_responses": ["visible"]}, eval_mode="ref_nlg")
        with tempfile.TemporaryDirectory() as td:
            tasks = Path(td) / "tasks.jsonl"
            results = Path(td) / "results.jsonl"
            save_tasks([task], str(tasks))
            await run_suite(str(tasks), Model(), str(results))
            row = json.loads(results.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(row["generation"]["finish_reason"], "stop")
        self.assertEqual(row["generation"]["prompt_tokens"], 3)
        self.assertEqual(row["generation"]["request"]["temperature"], 0.0)
        self.assertEqual(row["generation"]["request"]["max_tokens"], 8)

    def test_make_subject_passes_full_decoding_config(self):
        cfg = {"subjects": {"s": {
            "base_url": "http://example.test/v1", "api_key": "none", "model": "m",
            "temperature": 0.0, "max_tokens": 6144, "top_p": 1.0, "seed": 7,
            "presence_penalty": 0.0, "frequency_penalty": 0.0, "stop": ["END"],
            "extra_body": {"x": 1}, "max_model_len": 12288,
            "model_revision": "r1", "system_prompt_file": None,
        }}}
        client = _make_subject(cfg, "s")
        self.assertEqual(client.top_p, 1.0)
        self.assertEqual(client.seed, 7)
        self.assertEqual(client.stop, ["END"])
        self.assertEqual(client.extra_body, {"x": 1})
        self.assertEqual(client.max_model_len, 12288)
        self.assertEqual(client.model_revision, "r1")

    async def test_system_prompt_hash_is_recorded(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "choices": [{"finish_reason": "stop", "message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            })

        inner = ChatClient("http://example.test/v1", model="m",
                           transport=httpx.MockTransport(handler))
        client = SystemPromptChatClient(inner, "persona v2")
        result = await client.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(result.request["system_prompt_sha256"],
                         client.system_prompt_sha256)
        self.assertEqual(client.metadata()["system_prompt_chars"], len("persona v2"))


if __name__ == "__main__":
    unittest.main()
