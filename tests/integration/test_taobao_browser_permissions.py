"""使用本机真实 Chrome 与淘宝页面验证本机网络权限不再停留于询问状态。"""

import os
from uuid import uuid4

import pytest

from app.service.taobao_browser import playwright_runtime, taobao_browser_context, taobao_profile_root


@pytest.mark.skipif(
    os.getenv("TAOBAO_PERMISSION_BROWSER_TEST") != "1",
    reason="按需启用真实 Chrome 和淘宝网络权限验证",
)
@pytest.mark.parametrize("headless", [False, True])
def test_taobao_task_blocks_local_network_permission_on_every_tab(headless, caplog):
    task_name = f"permission-verification-{uuid4().hex}"
    profile = taobao_profile_root() / task_name
    with caplog.at_level("INFO", logger="kfc.taobao_browser"):
        with playwright_runtime() as playwright:
            with taobao_browser_context(
                playwright,
                task_name=task_name,
                headless=headless,
                show_window=False,
            ) as context:
                for url in ["https://s.taobao.com/", "https://www.taobao.com/"]:
                    page = context.new_page()
                    response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    assert response is not None and response.ok
                    states = page.evaluate("""async () => {
                        const states = {};
                        for (const name of ['local-network-access', 'local-network', 'loopback-network', 'notifications']) {
                            try { states[name] = (await navigator.permissions.query({name})).state; }
                            catch (error) { states[name] = 'unsupported'; }
                        }
                        return states;
                    }""")
                    supported = {
                        key: value for key, value in states.items() if key != "notifications" and value != "unsupported"
                    }
                    assert supported, "当前 Chrome 不支持本机网络权限查询，无法完成验证"
                    assert all(value == "denied" for value in supported.values()), states
                    assert states["notifications"] == "prompt", "无关权限不应被一并关闭"
                    page.close()
    assert not profile.exists(), "任务退出后应清理临时浏览器配置"
    assert "operation=configure_local_network result=denied" in caplog.text
