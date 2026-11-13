"""市场模板要求的 smoke test（文件名固定为 tests/test_smoke.py）。

N.E.K.O 的 ``check -r`` 在严格模式下要求仓库里存在这个文件，
并期望它至少能确认清单与入口没写错。真实行为测试在
``tests/test_plugin_runtime.py``。
"""

from pathlib import Path


def test_plugin_manifest_exists() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = root / "plugin.toml"
    assert manifest.is_file()
    text = manifest.read_text(encoding="utf-8")
    assert 'id = "qqbot_onebot_bridge"' in text
    assert 'entry = "plugin.plugins.qqbot_onebot_bridge:QQBotOneBotBridgePlugin"' in text


def test_entry_module_exists() -> None:
    root = Path(__file__).resolve().parents[1]
    assert (root / "__init__.py").is_file()


def test_vendor_bridge_bundled() -> None:
    root = Path(__file__).resolve().parents[1]
    for rel in (
        "vendor/__init__.py",
        "vendor/bridge/__init__.py",
        "vendor/bridge/client.py",
        "vendor/bridge/config.py",
        "vendor/bridge/events.py",
    ):
        assert (root / rel).is_file(), rel
