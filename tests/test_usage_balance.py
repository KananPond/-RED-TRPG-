"""用量账本与账户余额的回归测试（无网络依赖）。

覆盖四件事：
1. ``TokenMeter`` 的按日聚合——热力图与年度统计的唯一数据源，跨年不丢；
2. GitHub 贡献图布局——列 = 周、行 = 周日…周六，格子等级单调；
3. 余额解析——DeepSeek / Kimi 两种真实响应形状 + 自定义网关兜底；
4. 余额不可用时**不抛异常**，HTTP 层始终返回可读的 ``ok=False``。
"""

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import WebServerCase, strip_llm_env, restore_env  # noqa: E402

from trpg.config import LLMConfig                                  # noqa: E402
from trpg.harness import balance                                   # noqa: E402
from trpg.harness.usage import TokenMeter, estimate_cost          # noqa: E402

TODAY = date.today()


def ts_of(day: date, hour: int = 12) -> float:
    """某天中午的时间戳（本地时区，与 TokenMeter 的按日口径一致）。"""
    return time.mktime(day.timetuple()) + hour * 3600


class TestTokenMeterDaily(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.path = Path(self._td.name) / "usage.json"
        self.meter = TokenMeter(self.path)

    def tearDown(self):
        self._td.cleanup()

    def _record_day(self, day: date, prompt: int, completion: int, agent="dm"):
        return self.meter.record(agent, "deepseek-chat",
                                 {"prompt_tokens": prompt,
                                  "completion_tokens": completion,
                                  "total_tokens": prompt + completion},
                                 ts=ts_of(day))

    def test_daily_aggregation_survives_record_pruning(self):
        """明细只留最近 N 条，按日聚合不受影响（热力图的数据源）。"""
        for offset in (10, 5, 0):
            self._record_day(TODAY - timedelta(days=offset), 100, 50)
        self.meter.record("dm", "m", {"prompt_tokens": 1, "completion_tokens": 1},
                          ts=ts_of(TODAY))
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], 2)
        self.assertEqual(len(data["daily"]), 3)
        key = TODAY.isoformat()
        self.assertEqual(data["daily"][key]["calls"], 2)
        self.assertEqual(data["daily"][key]["total_tokens"], 152)

        # 把明细裁到 1 条后重新加载：daily 仍然完整
        data["records"] = data["records"][-1:]
        self.path.write_text(json.dumps(data), encoding="utf-8")
        again = TokenMeter(self.path)
        self.assertEqual(len(again.records), 1)
        self.assertEqual(len(again.daily), 3)

    def test_legacy_file_backfills_daily(self):
        """旧存档只有 records：加载时回填按日聚合。"""
        legacy = {"records": [
            {"ts": ts_of(TODAY), "agent": "dm", "model": "m", "steps": 1,
             "prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        ]}
        path = Path(self._td.name) / "legacy.json"
        path.write_text(json.dumps(legacy), encoding="utf-8")
        meter = TokenMeter(path)
        self.assertEqual(meter.daily[TODAY.isoformat()]["total_tokens"], 10)
        self.assertEqual(meter.daily[TODAY.isoformat()]["calls"], 1)

    def test_summary_windows_and_cost(self):
        self._record_day(TODAY, 1000, 1000)
        self._record_day(TODAY - timedelta(days=3), 500, 0)
        self._record_day(TODAY - timedelta(days=40), 9999, 9999)
        summary = self.meter.summary()
        self.assertEqual(summary["totals"]["total_tokens"], 2000 + 500 + 19998)
        self.assertEqual(summary["today"]["v"], 2000)
        self.assertEqual(summary["last_7"]["total_tokens"], 2000 + 500)
        self.assertEqual(summary["last_30"]["total_tokens"], 2000 + 500)
        self.assertEqual(summary["last_30"]["active_days"], 2)
        self.assertIn("deepseek-chat", summary["by_model"])
        self.assertGreater(summary["cost"]["cny"], 0)
        self.assertEqual(summary["cost"]["currency"], "CNY")

    def test_cost_uses_env_price_override(self):
        saved = {k: os.environ.get(k) for k in ("TRPG_PRICE_IN", "TRPG_PRICE_OUT")}
        try:
            os.environ["TRPG_PRICE_IN"] = "10"
            os.environ["TRPG_PRICE_OUT"] = "20"
            # 100 万输入 + 100 万输出 = 10 + 20 元
            self.assertAlmostEqual(estimate_cost(1_000_000, 1_000_000), 30.0, places=4)
            summary = self.meter.summary()
            self.assertEqual(summary["cost"]["price_in"], 10.0)
            self.assertEqual(summary["cost"]["price_out"], 20.0)
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_clear_wipes_daily_too(self):
        self._record_day(TODAY, 10, 10)
        self.meter.clear()
        summary = self.meter.summary()
        self.assertEqual(summary["totals"]["total_tokens"], 0)
        self.assertEqual(summary["heatmap"]["active_days"], 0)
        self.assertEqual(summary["heatmap"]["total"], 0)
        self.assertEqual(len(TokenMeter(self.path).daily), 0)


class TestHeatmapLayout(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.meter = TokenMeter(Path(self._td.name) / "u.json")

    def tearDown(self):
        self._td.cleanup()

    def test_weeks_are_sunday_aligned_and_within_range(self):
        heat = self.meter.heatmap(days=371)
        self.assertEqual(len(heat["weeks"]), 53)
        for week in heat["weeks"]:
            self.assertEqual(len(week), 7)
        cells = [c for w in heat["weeks"] for c in w if c]
        self.assertTrue(cells)
        first = date.fromisoformat(cells[0]["d"])
        last = date.fromisoformat(cells[-1]["d"])
        # 首格是周日（weekday()==6），末格落在结束日之后或等于结束日的那一周
        self.assertEqual(first.weekday(), 6)
        self.assertGreaterEqual(last, TODAY)
        self.assertLessEqual((last - TODAY).days, 6)
        self.assertEqual(heat["start"], (TODAY - timedelta(days=371 - 1)).isoformat())
        self.assertEqual(heat["end"], TODAY.isoformat())

    def test_levels_and_totals(self):
        for i, tokens in enumerate((10, 100, 1000, 10000)):
            self.meter.record("dm", "m", {"prompt_tokens": tokens,
                                          "completion_tokens": 0},
                              ts=ts_of(TODAY - timedelta(days=i)))
        heat = self.meter.heatmap()
        self.assertEqual(heat["max"], 10000)
        self.assertEqual(heat["active_days"], 4)
        self.assertEqual(heat["total"], 10 + 100 + 1000 + 10000)
        cells = [c for w in heat["weeks"] for c in w if c and c["v"]]
        ordered = sorted((c["v"], c["l"]) for c in cells)   # 按用量从小到大
        # 用量越大等级越高（sqrt 分档，单调不减）：10 → 1 级，10000 → 4 级
        self.assertEqual([lv for _, lv in ordered],
                         sorted(lv for _, lv in ordered))
        self.assertEqual(ordered[0][1], 1)
        self.assertEqual(ordered[-1][1], 4)

    def test_streaks_count_consecutive_days(self):
        for offset in range(3):
            self.meter.record("dm", "m", {"total_tokens": 100},
                              ts=ts_of(TODAY - timedelta(days=offset)))
        heat = self.meter.heatmap()
        self.assertEqual(heat["streak"], 3)
        self.assertEqual(heat["longest"], 3)

    def test_empty_meter_is_safe(self):
        heat = self.meter.heatmap()
        self.assertEqual(heat["max"], 0)
        self.assertEqual(heat["active_days"], 0)
        self.assertEqual(heat["today"]["l"], 0)


class TestBalanceParsing(unittest.TestCase):
    def test_deepseek_payload(self):
        parsed = balance.parse_balance("deepseek", {
            "is_available": True,
            "balance_infos": [{
                "currency": "CNY", "total_balance": "110.00",
                "granted_balance": "10.00", "topped_up_balance": "100.00",
            }],
        })
        self.assertEqual(parsed["total"], 110.0)
        self.assertEqual(parsed["currency"], "CNY")
        self.assertEqual(parsed["currency_sign"], "¥")
        self.assertTrue(parsed["available"])
        self.assertEqual([i["label"] for i in parsed["items"]], ["赠送余额", "充值余额"])

    def test_kimi_payload(self):
        parsed = balance.parse_balance("kimi", {
            "code": 0, "status": True,
            "data": {"available_balance": 49.58895,
                     "voucher_balance": 3.0, "cash_balance": 46.58895},
        })
        self.assertAlmostEqual(parsed["total"], 49.5889, places=4)
        self.assertTrue(parsed["available"])
        self.assertEqual(len(parsed["items"]), 2)

    def test_generic_payload_falls_back_to_key_search(self):
        parsed = balance.parse_balance("custom", {
            "result": {"account": {"remaining": "12.5", "currency": "USD"}}})
        self.assertEqual(parsed["total"], 12.5)
        self.assertEqual(parsed["currency"], "USD")
        self.assertEqual(parsed["currency_sign"], "$")

    def test_unrecognised_payload_raises(self):
        with self.assertRaises(ValueError):
            balance.parse_balance("custom", {"hello": "world"})

    def test_endpoint_mapping(self):
        def cfg(provider, base):
            return LLMConfig(base_url=base, api_key="k", model="m", provider=provider)

        self.assertEqual(balance.balance_endpoint(cfg("deepseek", "https://api.deepseek.com/v1")),
                         "https://api.deepseek.com/user/balance")
        self.assertEqual(balance.balance_endpoint(cfg("kimi", "https://api.moonshot.cn/v1")),
                         "https://api.moonshot.cn/v1/users/me/balance")
        self.assertIsNone(balance.balance_endpoint(cfg("glm", "https://open.bigmodel.cn/api/paas/v4")))
        self.assertIsNone(balance.balance_endpoint(None))

    def test_env_override_endpoint(self):
        saved = os.environ.get("TRPG_BALANCE_URL")
        try:
            os.environ["TRPG_BALANCE_URL"] = "https://gateway.example.com/v1/balance"
            cfg = LLMConfig(base_url="https://x.example.com/v1", api_key="k",
                            model="m", provider="custom")
            self.assertEqual(balance.balance_endpoint(cfg),
                             "https://gateway.example.com/v1/balance")
        finally:
            if saved is None:
                os.environ.pop("TRPG_BALANCE_URL", None)
            else:
                os.environ["TRPG_BALANCE_URL"] = saved


class TestFetchBalanceOffline(unittest.TestCase):
    """不联网：只验证缺配置 / 无接口 / 端点非法这三条早退路径。"""

    def setUp(self):
        self._env = strip_llm_env()
        self._saved_balance_url = os.environ.pop("TRPG_BALANCE_URL", None)

    def tearDown(self):
        if self._saved_balance_url is not None:
            os.environ["TRPG_BALANCE_URL"] = self._saved_balance_url
        restore_env(self._env)

    def test_no_config(self):
        result = balance.fetch_balance(None)
        self.assertFalse(result["ok"])
        self.assertIn("尚未接入", result["error"])

    def test_provider_without_balance_api(self):
        cfg = LLMConfig(base_url="https://open.bigmodel.cn/api/paas/v4",
                        api_key="k", model="glm-4.6", provider="glm")
        result = balance.fetch_balance(cfg)
        self.assertFalse(result["ok"])
        self.assertTrue(result.get("unsupported"))
        self.assertIn("TRPG_BALANCE_URL", result["hint"])

    def test_rejects_non_http_endpoint(self):
        saved = os.environ.get("TRPG_BALANCE_URL")
        try:
            os.environ["TRPG_BALANCE_URL"] = "file:///etc/passwd"
            cfg = LLMConfig(base_url="https://api.deepseek.com/v1", api_key="k",
                            model="m", provider="deepseek")
            result = balance.fetch_balance(cfg)
            self.assertFalse(result["ok"])
            self.assertIn("校验失败", result["error"])
        finally:
            if saved is None:
                os.environ.pop("TRPG_BALANCE_URL", None)
            else:
                os.environ["TRPG_BALANCE_URL"] = saved

    def test_api_key_never_echoed(self):
        cfg = LLMConfig(base_url="https://open.bigmodel.cn/api/paas/v4",
                        api_key="sk-super-secret", model="glm-4.6", provider="glm")
        result = balance.fetch_balance(cfg)
        self.assertNotIn("sk-super-secret", json.dumps(result, ensure_ascii=False))


class TestUsageHttpEndpoints(WebServerCase):
    """HTTP 层：/api/usage 下发热力图，/api/balance 在无 LLM 时给出可读原因。"""

    def test_usage_payload_shape(self):
        self.app.meter.record("dm", "deepseek-chat",
                              {"prompt_tokens": 120, "completion_tokens": 30,
                               "total_tokens": 150})
        status, body = self.get("/api/usage")
        self.assertEqual(status, 200)
        self.assertEqual(body["totals"]["total_tokens"], 150)
        self.assertEqual(body["today"]["v"], 150)
        heat = body["heatmap"]
        self.assertEqual(len(heat["weeks"]), 53)
        self.assertEqual(heat["active_days"], 1)
        self.assertEqual(heat["today"]["v"], 150)
        self.assertIn("cost", body)

    def test_usage_days_query(self):
        status, body = self.get("/api/usage?days=30")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["heatmap"]["weeks"]), 5)

    def test_balance_without_llm_is_readable(self):
        status, body = self.get("/api/balance")
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])
        self.assertIn("尚未接入", body["error"])
        self.assertIn("hint", body)
        self.assertFalse(body["cached"])

    def test_usage_clear_also_clears_heatmap(self):
        self.app.meter.record("dm", "m", {"total_tokens": 10})
        self.post("/api/usage/clear", {})
        status, body = self.get("/api/usage")
        self.assertEqual(status, 200)
        self.assertEqual(body["heatmap"]["active_days"], 0)
        self.assertEqual(body["totals"]["total_tokens"], 0)


if __name__ == "__main__":
    unittest.main()
