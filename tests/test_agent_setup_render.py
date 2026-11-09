"""Agent setup's command line preserves transports and unrelated client configuration."""

import json
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest


@pytest.mark.integration
@pytest.mark.timeout(5)
@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_renderer_cli_keeps_http_and_stdio_servers(tmp_path, agent):
    source = Path(__file__).resolve().parents[1] / ".agents/skills/agent-setup/scripts/render.py"
    script = tmp_path / ".agents/skills/agent-setup/scripts/render.py"
    script.parent.mkdir(parents=True)
    shutil.copyfile(source, script)
    config = tmp_path / ".agents/mcp/servers.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps(
            {
                "servers": {
                    "memory": {
                        "type": "http",
                        "url": "http://127.0.0.1:8766/mcp",
                        "codex": {"approval": {"write_note": "approve"}},
                    },
                    "local": {"command": "local-server", "args": ["mcp"], "env": {"OFFLINE": "1"}},
                }
            }
        )
    )
    target = tmp_path / ("client.json" if agent == "claude" else "client.toml")
    if agent == "codex":
        target.write_text('model = "existing"\n')

    result = subprocess.run(
        ["uv", "run", "--script", str(script), "--agent", agent, "--out", str(target)],
        capture_output=True,
        text=True,
        timeout=3,
    )

    assert result.returncode == 0, result.stderr
    if agent == "claude":
        assert json.loads(target.read_text()) == {
            "mcpServers": {
                "memory": {"type": "http", "url": "http://127.0.0.1:8766/mcp"},
                "local": {"command": "local-server", "args": ["mcp"], "env": {"OFFLINE": "1"}},
            }
        }
    else:
        assert tomllib.loads(target.read_text()) == {
            "model": "existing",
            "mcp_servers": {
                "memory": {
                    "url": "http://127.0.0.1:8766/mcp",
                    "tools": {"write_note": {"approval_mode": "approve"}},
                },
                "local": {"command": "local-server", "args": ["mcp"], "env": {"OFFLINE": "1"}},
            },
        }
