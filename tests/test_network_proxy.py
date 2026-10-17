"""出网方式（代理）回归测试——无网络。

2026-10 的真实故障：服务进程继承了终端/策略代理的 ``HTTP_PROXY`` /
``HTTPS_PROXY``，urllib 照单全收，把请求送进 ``CONNECT`` 隧道，代理回 403，
于是设置页的「拉取模型列表」「测试连接」都报

    URLError: Tunnel connection failed: 403 Forbidden

而这条 403 来自**代理**，请求根本没到供应商——换密钥、改模型、切协议都不管用。
修法有三层，这里逐层钉住：

1. 默认**直连**（忽略进程里的代理），要代理必须用 ``TRPG_LLM_PROXY`` 明示；
2. 继承来的代理隧道失败时，自动用直连重试一次（显式指定的代理不回落）；
3. 失败提示直接说清「是代理挡的」以及该改哪个开关。
"""

import io
import os
import sys
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import WebServerCase                              # noqa: E402

from trpg.harness import adapter as A                          # noqa: E402
from trpg.harness.adapter import OpenAICompatibleAdapter       # noqa: E402

TUNNEL_403 = "<urlopen error Tunnel connection failed: 403 Forbidden>"


class _HTTPError(urllib.error.HTTPError):
    def __init__(self, code=401):
        super().__init__("https://api.deepseek.com/models", code, "err", {},
                         io.BytesIO(b'{"error": {"message": "unauthorized"}}'))


def _opener(exc=None, result="ok"):
    """假 opener：记录调用次数，可选抛异常。"""
    class _O:
        def __init__(self):
            self.calls = 0

        def open(self, req, timeout=None):
            self.calls += 1
            if exc is not None:
                raise exc
            return result
    return _O()


def _proxies_in(opener) -> dict:
    """汇总 opener 实际生效的代理表（ProxyHandler 只在有代理时才进 handlers）。"""
    found: dict = {}
    for handler in opener.handlers:
        if isinstance(handler, urllib.request.ProxyHandler):
            found.update(getattr(handler, "proxies", {}) or {})
    return found


# ================================================================ 代理开关
class _ProxyEnvCase(unittest.TestCase):
    """临时改写 TRPG_LLM_PROXY，并让模块级 opener 在收尾时回到真实设置。"""

    def setUp(self):
        self._saved_env = os.environ.get(A.PROXY_ENV)
        os.environ.pop(A.PROXY_ENV, None)

    def tearDown(self):
        if self._saved_env is None:
            os.environ.pop(A.PROXY_ENV, None)
        else:
            os.environ[A.PROXY_ENV] = self._saved_env
        A._current_openers()          # 环境变量复原 → 重建真实 opener


class TestProxySetting(_ProxyEnvCase):
    def test_default_is_direct(self):
        # 不设变量 = 直连：这正是「别把终端代理塞给 LLM 请求」的默认值
        self.assertEqual(A.proxy_setting(), ("direct", ""))
        self.assertEqual(A.network_info()["mode"], "direct")

    def test_direct_words(self):
        for word in ("direct", "none", "off", "no", "0", " DIRECT "):
            os.environ[A.PROXY_ENV] = word
            self.assertEqual(A.proxy_setting(), ("direct", ""), word)

    def test_env_mode_inherits_system_proxy(self):
        os.environ[A.PROXY_ENV] = "env"
        self.assertEqual(A.proxy_setting(), ("env", ""))
        self.assertEqual(A.network_info()["mode"], "env")

    def test_explicit_proxy_address(self):
        os.environ[A.PROXY_ENV] = "http://127.0.0.1:7890"
        self.assertEqual(A.proxy_setting(), ("explicit", "http://127.0.0.1:7890"))
        info = A.network_info()
        self.assertEqual(info["mode"], "explicit")
        self.assertIn("127.0.0.1:7890", info["label"])

    def test_bare_host_port_gets_scheme(self):
        os.environ[A.PROXY_ENV] = "127.0.0.1:7890"
        self.assertEqual(A.proxy_setting(), ("explicit", "http://127.0.0.1:7890"))

    def test_direct_mode_reports_ignored_env_proxy(self):
        # 直连时把「检测到但被忽略」的环境代理显示出来：用户才看得懂 Tunnel 403
        os.environ[A.PROXY_ENV] = "direct"
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:51175"
        try:
            info = A.network_info()
        finally:
            os.environ.pop("HTTPS_PROXY", None)
        self.assertEqual(info["mode"], "direct")
        self.assertIn("127.0.0.1:51175", info["ignored_env_proxy"])

    def test_opener_follows_setting(self):
        # 直连模式的 opener 里不能残留任何代理；env 模式则必须带上环境代理
        direct, fallback = A._build_openers("direct", "")
        self.assertIsNone(fallback)
        self.assertEqual(_proxies_in(direct), {})
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"
        try:
            env, _ = A._build_openers("env", "")
        finally:
            os.environ.pop("HTTPS_PROXY", None)
        self.assertIn("https", _proxies_in(env))

    def test_only_env_mode_has_direct_fallback(self):
        self.assertIsNone(A._build_openers("direct", "")[1])
        self.assertIsNone(A._build_openers("explicit", "http://p:1")[1])
        self.assertIsNotNone(A._build_openers("env", "")[1])


# ================================================================ 隧道回落
class TestTunnelFallback(unittest.TestCase):
    def setUp(self):
        self._saved = (A._OPENER, A._FALLBACK_OPENER, A._OPENER_KEY)

    def tearDown(self):
        A._OPENER, A._FALLBACK_OPENER, A._OPENER_KEY = self._saved

    def _patch(self, proxy_opener, fallback_opener=None):
        A._OPENER = proxy_opener
        A._FALLBACK_OPENER = fallback_opener
        A._OPENER_KEY = A.proxy_setting()     # 防止 open_url 按环境重建而覆盖补丁

    def test_tunnel_failure_retries_direct(self):
        proxy = _opener(exc=urllib.error.URLError(TUNNEL_403))
        direct = _opener(result="response")
        self._patch(proxy, direct)
        req = urllib.request.Request("https://api.deepseek.com/models")
        self.assertEqual(A.open_url(req, timeout=1), "response")
        self.assertEqual((proxy.calls, direct.calls), (1, 1))

    def test_direct_mode_does_not_retry(self):
        proxy = _opener(exc=urllib.error.URLError(TUNNEL_403))
        self._patch(proxy, None)
        req = urllib.request.Request("https://api.deepseek.com/models")
        with self.assertRaises(urllib.error.URLError):
            A.open_url(req, timeout=1)
        self.assertEqual(proxy.calls, 1)

    def test_http_error_is_not_retried(self):
        # 供应商真实回了 401/400：这是配置问题，换条路由重试没有意义
        proxy = _opener(exc=_HTTPError(401))
        direct = _opener()
        self._patch(proxy, direct)
        req = urllib.request.Request("https://api.deepseek.com/models")
        with self.assertRaises(urllib.error.HTTPError):
            A.open_url(req, timeout=1)
        self.assertEqual(direct.calls, 0)

    def test_non_proxy_network_error_is_not_retried(self):
        proxy = _opener(exc=urllib.error.URLError("connection refused"))
        direct = _opener()
        self._patch(proxy, direct)
        req = urllib.request.Request("https://api.deepseek.com/models")
        with self.assertRaises(urllib.error.URLError):
            A.open_url(req, timeout=1)
        self.assertEqual(direct.calls, 0)

    def test_adapter_message_names_the_switch(self):
        # 终端版（--check-llm）也看得懂：错误里直接点名 TRPG_LLM_PROXY
        saved_validate = A._assert_host_allowed
        A._assert_host_allowed = lambda host: None
        try:
            self._patch(_opener(exc=urllib.error.URLError(TUNNEL_403)), None)
            ad = OpenAICompatibleAdapter("https://api.deepseek.com", "k", max_retries=1)
            with self.assertRaises(RuntimeError) as ctx:
                ad.chat("x", [{"role": "user", "content": "hi"}], None, 0.0, 8)
        finally:
            A._assert_host_allowed = saved_validate
        text = str(ctx.exception)
        self.assertIn("Tunnel connection failed", text)
        self.assertIn("TRPG_LLM_PROXY", text)


# ================================================================ 设置视图 / 提示
class TestSettingsReportsNetwork(WebServerCase):
    def test_settings_view_carries_network_mode(self):
        status, body = self.get("/api/settings/llm")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["network"]["mode"], "direct")
        self.assertTrue(body["network"]["label"])


class _TunnelFailureCase(WebServerCase):
    """把适配器的出网口堵成「代理 403 隧道」，看服务端怎么解释与回报。"""

    def setUp(self):
        super().setUp()
        self._saved_validate = A._assert_host_allowed
        self._saved_openers = (A._OPENER, A._FALLBACK_OPENER, A._OPENER_KEY)
        A._assert_host_allowed = lambda host: None          # 离线：跳过 DNS 边界校验
        A._OPENER = _opener(exc=urllib.error.URLError(TUNNEL_403))
        A._FALLBACK_OPENER = None                            # 回落也不许真的出网
        A._OPENER_KEY = A.proxy_setting()
        status, body = self.post("/api/settings/llm", {
            "provider": "deepseek", "base_url": "https://api.deepseek.com",
            "model": "deepseek-flash", "api_key": "sk-not-real",
            "thinking": "off", "api_style": "chat"})
        self.assertEqual(status, 200, body)

    def tearDown(self):
        A._assert_host_allowed = self._saved_validate
        A._OPENER, A._FALLBACK_OPENER, A._OPENER_KEY = self._saved_openers
        super().tearDown()


class TestTunnelFailureHints(_TunnelFailureCase):
    def test_models_listing_explains_proxy(self):
        status, body = self.post("/api/models", {"provider": "deepseek"})
        self.assertEqual(status, 200, body)
        self.assertFalse(body["ok"])
        self.assertIn("Tunnel connection failed", body["error"])
        self.assertIn("TRPG_LLM_PROXY", body["hint"])
        self.assertEqual(body["network"]["mode"], "direct")

    def test_connection_test_explains_proxy(self):
        status, body = self.post("/api/llm/test", {})
        self.assertEqual(status, 400, body)
        self.assertIn("TRPG_LLM_PROXY", body["hint"])
        self.assertIn("network", body)


if __name__ == "__main__":
    unittest.main()
