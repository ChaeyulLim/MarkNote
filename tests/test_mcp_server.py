"""실제로 `python -m app.mcp_server` 를 띄워 MCP(JSON-RPC over stdio)로 대화해 본다."""

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def mcp(tmp_path):
    def start(allow_edit=True, allow_deck_edit=False, allow_delete=False):
        env = dict(
            os.environ,
            PYTHONPATH=ROOT,
            PYTHONIOENCODING="utf-8",
            DATA_DIR=str(tmp_path / "data"),
            MARKNOTE_RUN_ID="run-mcp",
            MARKNOTE_ALLOW_EDIT="1" if allow_edit else "0",
            MARKNOTE_ALLOW_DECK_EDIT="1" if allow_deck_edit else "0",
            MARKNOTE_ALLOW_DELETE="1" if allow_delete else "0",
        )
        proc = subprocess.Popen(
            [sys.executable, "-m", "app.mcp_server"],
            cwd=str(tmp_path),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
        )
        procs.append(proc)

        def rpc(method, params=None, msg_id=None):
            msg = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                msg["params"] = params
            if msg_id is not None:
                msg["id"] = msg_id
            proc.stdin.write(json.dumps(msg) + "\n")
            proc.stdin.flush()
            return json.loads(proc.stdout.readline()) if msg_id is not None else None

        rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}, 1)
        rpc("notifications/initialized")
        return rpc

    procs = []
    yield start
    for proc in procs:
        proc.stdin.close()
        proc.wait(timeout=10)


def _call(rpc, name, args, msg_id):
    res = rpc("tools/call", {"name": name, "arguments": args}, msg_id)["result"]
    return json.loads(res["content"][0]["text"])


def test_tools_and_direct_edit_roundtrip(mcp, tmp_path):
    rpc = mcp(allow_edit=True)
    names = [t["name"] for t in rpc("tools/list", {}, 2)["result"]["tools"]]
    assert names == ["search_pages", "find_page", "list_pages", "read_page", "edit_page", "create_page", "list_decks", "read_deck"]

    # parent_id 없이 만들면 맨 위 낱개 노트
    assert _call(rpc, "create_page", {"title": "맨위", "markdown": "# 안녕"}, 3)["file"] == "맨위.md"
    project = _call(rpc, "create_page", {"title": "프로젝트", "markdown": "", "folder": True}, 6)
    created = _call(rpc, "create_page", {"title": "테스트", "markdown": "# 안녕", "parent_id": project["page_id"]}, 7)
    assert created["applied"] is True and created["file"] == "프로젝트/테스트.md"
    assert (tmp_path / "data" / "notes" / "프로젝트" / "테스트.md").read_text(encoding="utf-8").strip() == "# 안녕"
    assert [p["title"] for p in _call(rpc, "list_pages", {}, 4)["pages"]] == ["프로젝트", "테스트", "맨위"]  # 바로 만들어짐
    assert "찾을 수 없습니다" in _call(rpc, "read_page", {"page_id": 99}, 5)["error"]


def test_edit_tools_hidden_without_permission(mcp):
    rpc = mcp(allow_edit=False)
    names = [t["name"] for t in rpc("tools/list", {}, 2)["result"]["tools"]]
    assert names == ["search_pages", "find_page", "list_pages", "read_page", "list_decks", "read_deck"]


def test_trash_tool_only_with_permission(mcp, tmp_path):
    rpc = mcp(allow_edit=True, allow_delete=True)
    names = [t["name"] for t in rpc("tools/list", {}, 2)["result"]["tools"]]
    assert "trash_page" in names
    project = _call(rpc, "create_page", {"title": "프로젝트", "markdown": "", "folder": True}, 3)
    note = _call(rpc, "create_page", {"title": "지울것", "markdown": "x", "parent_id": project["page_id"]}, 4)
    res = _call(rpc, "trash_page", {"page_id": note["page_id"], "summary": "요청"}, 5)
    assert res["applied"] is True and res["file"] == "프로젝트/지울것.md"
    notes = tmp_path / "data" / "notes"
    assert not (notes / "프로젝트" / "지울것.md").exists()
    assert len(list((notes / ".trash").rglob("지울것.md"))) == 1  # 휴지통 폴더로 옮겨짐


def test_deck_edit_tool(mcp):
    rpc = mcp(allow_edit=False, allow_deck_edit=True)
    names = [t["name"] for t in rpc("tools/list", {}, 2)["result"]["tools"]]
    assert names[-1] == "edit_deck"
    assert _call(rpc, "list_decks", {}, 3) == {"decks": []}
    result = _call(rpc, "edit_deck", {"deck_id": 1, "ops": [{"op": "delete_slide", "slide": 1}], "summary": "x"}, 4)
    assert "찾을 수 없습니다" in result["error"]
