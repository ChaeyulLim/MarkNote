import io
import os

from PIL import Image


def create_page(client, **body):
    res = client.post("/api/pages", json=body)
    assert res.status_code == 201
    return res.get_json()["page"]


def new_note(client, title=""):
    """블록을 쓸 수 있는 노트 (프로젝트 = 폴더 안)."""
    project = create_page(client, title="프로젝트")
    return create_page(client, title=title, parent_id=project["id"])


def blocks_of(client, page_id):
    return client.get(f"/api/pages/{page_id}/source").get_json()["markdown"]


def test_create_page_and_view(auth_client):
    page = create_page(auth_client, title="첫 페이지")
    res = auth_client.get(f"/p/{page['id']}")  # 번호 주소 → 경로 주소로
    assert res.status_code == 302 and res.headers["Location"] == page["url"]
    assert page["url"] == "/n/%EC%B2%AB%20%ED%8E%98%EC%9D%B4%EC%A7%80/"
    res = auth_client.get(page["url"])
    assert res.status_code == 200 and "첫 페이지" in res.get_data(as_text=True)
    assert auth_client.get("/n/첫 페이지/없는.md").status_code == 404


def test_folders_nest_and_notes_live_in_folders(auth_client):
    project = create_page(auth_client, title="부모")
    sub = create_page(auth_client, title="폴더", parent_id=project["id"], kind="folder")
    note = create_page(auth_client, title="자식", parent_id=sub["id"])
    assert note["path"] == "부모/폴더/자식.md"
    assert auth_client.post("/api/pages", json={"title": "x", "parent_id": note["id"]}).status_code == 400  # 노트 아래 X
    html = auth_client.get(note["url"]).get_data(as_text=True)
    assert "부모" in html and "폴더" in html  # 브레드크럼
    assert "부모/폴더/자식.md" in html  # 파일 위치
    folder_html = auth_client.get(project["url"]).get_data(as_text=True)
    assert "폴더/" in folder_html and "설명 추가" in folder_html


def test_relative_links_render_as_note_addresses(auth_client):
    project = create_page(auth_client, title="회사")
    target = create_page(auth_client, title="회의록", parent_id=project["id"])
    note = create_page(auth_client, title="목차", parent_id=project["id"])
    res = auth_client.put(f"/api/pages/{note['id']}/source", json={"markdown": "[회의](회의록.md) [없음](없는.md)"})
    html = res.get_json()["blocks"][0]["html"]
    assert f'href="{target["url"]}"' in html
    assert 'class="broken-link"' in html


def test_reorder_and_move_by_drag(auth_client):
    project = create_page(auth_client, title="회사")
    a = create_page(auth_client, title="가", parent_id=project["id"])
    b = create_page(auth_client, title="나", parent_id=project["id"])
    sub = create_page(auth_client, title="폴더", parent_id=project["id"], kind="folder")
    auth_client.patch(f"/api/pages/{b['id']}", json={"order": [b["id"], a["id"]]})
    html = auth_client.get(project["url"]).get_data(as_text=True)
    assert html.index("폴더/") < html.index(">나<") < html.index(">가<")  # 폴더 먼저, 그다음 정한 순서
    res = auth_client.patch(f"/api/pages/{a['id']}", json={"parent_id": sub["id"]}).get_json()
    assert res["path"] == "회사/폴더/가.md" and res["url"].startswith("/n/")


def test_project_is_a_folder(auth_client, app):
    project = create_page(auth_client, title="회사")
    note = create_page(auth_client, title="회의록", parent_id=project["id"])
    notes_dir = os.path.join(app.config["NOTES_DIR"], "회사")
    assert os.listdir(notes_dir) == ["회의록.md"]  # instance/notes/회사/회의록.md
    assert note["path"] == "회사/회의록.md"
    # 프로젝트에는 내용을 쓸 수 없다
    assert auth_client.put(f"/api/pages/{project['id']}/source", json={"markdown": "x"}).status_code == 400
    html = auth_client.get(project["url"]).get_data(as_text=True)
    assert "회의록.md" in html and 'id="blocks"' not in html  # 편집기 대신 노트 목록
    # 내용 있는 노트는 최상위(프로젝트 자리)로 못 옮김
    auth_client.put(f"/api/pages/{note['id']}/source", json={"markdown": "내용"})
    assert auth_client.patch(f"/api/pages/{note['id']}", json={"parent_id": None}).status_code == 400


def test_block_crud_and_split(auth_client):
    page = new_note(auth_client)
    res = auth_client.post(f"/api/pages/{page['id']}/blocks", json={"content": "a"})
    block_id = res.get_json()["block"]["id"]

    res = auth_client.patch(f"/api/blocks/{block_id}", json={"content": "하나\n\n둘\n\n셋"})
    blocks = res.get_json()["blocks"]
    assert [b["content"] for b in blocks] == ["하나", "둘", "셋"]
    assert blocks[0]["id"] == block_id

    auth_client.post(f"/api/pages/{page['id']}/blocks/reorder", json={"order": [b["id"] for b in reversed(blocks)]})
    assert blocks_of(auth_client, page["id"]) == "셋\n\n둘\n\n하나"

    auth_client.delete(f"/api/blocks/{blocks[1]['id']}")
    assert blocks_of(auth_client, page["id"]) == "셋\n\n하나"


def test_live_save_keeps_block_unsplit(auth_client):
    page = new_note(auth_client)
    block = auth_client.post(f"/api/pages/{page['id']}/blocks", json={"content": "a"}).get_json()["block"]
    # 입력 중 자동 저장: 빈 줄이 있어도 나누지 않음 (편집 화면이 바뀌지 않게)
    res = auth_client.patch(f"/api/blocks/{block['id']}", json={"content": "하나\n\n둘", "split": False})
    assert [b["content"] for b in res.get_json()["blocks"]] == ["하나\n\n둘"]
    # 편집을 마칠 때의 저장은 평소처럼 나눔
    res = auth_client.patch(f"/api/blocks/{block['id']}", json={"content": "하나\n\n둘"})
    assert [b["content"] for b in res.get_json()["blocks"]] == ["하나", "둘"]


def test_insert_block_after_and_before(auth_client):
    page = new_note(auth_client)
    a = auth_client.post(f"/api/pages/{page['id']}/blocks", json={"content": "A"}).get_json()["block"]
    auth_client.post(f"/api/pages/{page['id']}/blocks", json={"content": "C"})
    auth_client.post(f"/api/pages/{page['id']}/blocks", json={"content": "B", "after_id": a["id"]})
    auth_client.post(f"/api/pages/{page['id']}/blocks", json={"content": "0", "before_id": a["id"]})
    assert blocks_of(auth_client, page["id"]) == "0\n\nA\n\nB\n\nC"


def test_source_roundtrip(auth_client):
    page = new_note(auth_client)
    auth_client.put(f"/api/pages/{page['id']}/source", json={"markdown": "# 제목\n\n본문"})
    assert blocks_of(auth_client, page["id"]) == "# 제목\n\n본문"


def test_archive_and_trash_cascade_and_restore(auth_client):
    parent = create_page(auth_client, title="부모")
    child = create_page(auth_client, title="자식", parent_id=parent["id"])
    assert auth_client.get(f"/p/{child['id']}").status_code == 302

    auth_client.post(f"/api/pages/{parent['id']}/archive")
    assert "부모" in auth_client.get("/archive").get_data(as_text=True)
    assert auth_client.patch(f"/api/pages/{child['id']}", json={}).get_json()["page"]["status"] == "archived"

    auth_client.post(f"/api/pages/{parent['id']}/restore")
    assert auth_client.patch(f"/api/pages/{child['id']}", json={}).get_json()["page"]["status"] == "active"

    auth_client.post(f"/api/pages/{parent['id']}/trash")
    # 자식만 복원하면 부모가 휴지통에 있으므로 "분류 안 됨" 프로젝트로 나온다
    restored = auth_client.post(f"/api/pages/{child['id']}/restore").get_json()["page"]
    assert restored["status"] == "active"
    inbox = auth_client.patch(f"/api/pages/{restored['parent_id']}", json={}).get_json()["page"]
    assert inbox["title"] == "분류 안 됨" and inbox["parent_id"] is None


def test_delete_requires_trash_and_removes_subtree(auth_client):
    parent = create_page(auth_client)
    child = create_page(auth_client, parent_id=parent["id"])
    assert auth_client.delete(f"/api/pages/{parent['id']}").status_code == 400

    auth_client.post(f"/api/pages/{parent['id']}/trash")
    assert auth_client.delete(f"/api/pages/{parent['id']}").status_code == 200
    assert auth_client.get(f"/p/{child['id']}").status_code == 404


def test_cannot_move_page_under_its_descendant(auth_client):
    parent = create_page(auth_client)
    child = create_page(auth_client, parent_id=parent["id"])
    res = auth_client.patch(f"/api/pages/{parent['id']}", json={"parent_id": child["id"]})
    assert res.status_code == 400


def _png_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "red").save(buf, format="PNG")
    return buf.getvalue()


def test_image_upload_and_serve(auth_client, app):
    page = new_note(auth_client)
    res = auth_client.post(
        "/api/uploads",
        data={"file": (io.BytesIO(_png_bytes()), "사진 (1).png"), "page_id": page["id"]},
        content_type="multipart/form-data",
    )
    assert res.status_code == 201
    body = res.get_json()
    assert body["name"] == "사진 1"
    served = auth_client.get(body["url"])
    assert served.status_code == 200
    assert served.mimetype == "image/png"
    served.close()

    # 본문에 넣은 그림은 페이지를 영구 삭제하면 파일도 사라진다
    auth_client.put(f"/api/pages/{page['id']}/source", json={"markdown": f"![사진]({body['url']})"})
    auth_client.post(f"/api/pages/{page['id']}/trash")
    auth_client.delete(f"/api/pages/{page['id']}")
    assert auth_client.get(body["url"]).status_code == 404


def test_upload_rejects_non_images(auth_client):
    res = auth_client.post(
        "/api/uploads",
        data={"file": (io.BytesIO(b"<svg onload=alert(1)>"), "x.svg")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 400
    assert "이미지" in res.get_json()["error"]


def test_rename_moves_file_and_returns_path(auth_client, app):
    project = create_page(auth_client, title="회사")
    note = create_page(auth_client, title="회의록", parent_id=project["id"])
    auth_client.put(f"/api/pages/{note['id']}/source", json={"markdown": "내용"})
    res = auth_client.patch(f"/api/pages/{note['id']}", json={"title": "3월 회의"})
    assert res.get_json()["path"] == "회사/3월 회의.md"
    with open(os.path.join(app.config["NOTES_DIR"], "회사", "3월 회의.md"), encoding="utf-8") as f:
        assert f.read().strip() == "내용"
    # 같은 폴더에 같은 이름은 안 됨
    other = create_page(auth_client, title="다른", parent_id=project["id"])
    assert auth_client.patch(f"/api/pages/{other['id']}", json={"title": "3월 회의"}).status_code == 409


def test_file_changed_outside_app_is_picked_up(auth_client, app):
    project = create_page(auth_client, title="회사")
    note = create_page(auth_client, title="회의록", parent_id=project["id"])
    folder = os.path.join(app.config["NOTES_DIR"], "회사")
    with open(os.path.join(folder, "회의록.md"), "w", encoding="utf-8") as f:
        f.write("밖에서 고침\n\n둘째")
    with open(os.path.join(folder, "새 파일.md"), "w", encoding="utf-8") as f:
        f.write("새 노트")
    assert blocks_of(auth_client, note["id"]) == "밖에서 고침\n\n둘째"
    html = auth_client.get(project["url"]).get_data(as_text=True)
    assert "새 파일" in html


def test_folder_and_note_with_same_name(auth_client):
    project = create_page(auth_client, title="회사")
    create_page(auth_client, title="회의", parent_id=project["id"], kind="folder")
    note = create_page(auth_client, title="회의", parent_id=project["id"])  # 노트는 따로 됨
    assert note["path"] == "회사/회의.md"
    res = auth_client.post("/api/pages", json={"title": "회의", "parent_id": project["id"]})
    assert res.status_code == 409 and "노트" in res.get_json()["error"]
    memo = create_page(auth_client, title="메모", parent_id=project["id"])
    assert auth_client.patch(f"/api/pages/{memo['id']}", json={"title": "회의"}).status_code == 409
