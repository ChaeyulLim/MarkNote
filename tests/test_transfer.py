import io
import zipfile

from PIL import Image


def _png():
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "blue").save(buf, format="PNG")
    return buf.getvalue()


def _zip(files: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data.encode() if isinstance(data, str) else data)
    return buf.getvalue()


def _import(client, files, parent_id=None, on_conflict=None):
    data = {"files": [(io.BytesIO(content), name) for name, content in files]}
    if parent_id is not None:
        data["parent_id"] = str(parent_id)
    if on_conflict:
        data["on_conflict"] = on_conflict
    return client.post("/api/import", data=data, content_type="multipart/form-data")


def _source(client, page_id):
    return client.get(f"/api/pages/{page_id}/source").get_json()["markdown"]


def _children(app, page_id):
    from app.services import store

    with app.app_context():
        return {p.title: p.id for p in store.children(page_id)}


def test_download_single_markdown(auth_client):
    project = auth_client.post("/api/pages", json={"title": "회사", "kind": "folder"}).get_json()["page"]
    page = auth_client.post("/api/pages", json={"title": "회의록", "parent_id": project["id"]}).get_json()["page"]
    auth_client.put(f"/api/pages/{page['id']}/source", json={"markdown": "본문\n\n- [ ] 할 일\n\n[일정](일정.md)"})

    res = auth_client.get(f"/export/page/{page['id']}.md")
    assert res.status_code == 200
    assert "attachment" in res.headers["Content-Disposition"]
    assert res.get_data(as_text=True) == "본문\n\n- [ ] 할 일\n\n[일정](일정.md)\n"  # 파일 그대로
    # 폴더는 .md 대신 .zip
    res = auth_client.get(f"/export/page/{project['id']}.md")
    assert res.status_code == 302 and res.headers["Location"].endswith(f"/export/page/{project['id']}.zip")


def test_import_single_markdown_goes_to_top(auth_client):
    md = "---\nicon: 🚀\n---\n# whatever\n\n첫 문단\n\n둘째 문단\n"
    res = _import(auth_client, [("whatever.md", md.encode())])
    assert res.status_code == 201
    page = res.get_json()["pages"][0]
    assert page["title"] == "whatever" and page["kind"] == "note"
    assert _source(auth_client, page["id"]) == "첫 문단\n\n둘째 문단"  # 앞머리·파일 이름과 같은 # 제목은 뺌
    assert page["parent_id"] is None  # 맨 위에 그대로 (탐색기처럼)


def test_import_folder_keeps_readme_as_note(app, auth_client):
    archive = _zip({"회사/README.md": "회사 설명", "회사/회의록.md": "# 회의록\n\n내용", "회사/2026/일정.md": "일정"})
    res = _import(auth_client, [("backup.zip", archive)])
    root = res.get_json()["pages"][0]
    assert root["title"] == "회사" and root["parent_id"] is None and root["kind"] == "folder"
    kids = _children(app, root["id"])
    assert kids.keys() == {"2026", "README", "회의록"}  # README.md 도 그냥 노트
    assert _source(auth_client, kids["README"]) == "회사 설명"


def test_import_uses_filename_and_reads_cp949(auth_client):
    res = _import(auth_client, [("메모.txt", "윈도우 메모장 한글".encode("cp949"))])
    page = res.get_json()["pages"][0]
    assert page["title"] == "메모"
    assert _source(auth_client, page["id"]) == "윈도우 메모장 한글"


def test_import_into_folder(auth_client):
    project = auth_client.post("/api/pages", json={"title": "프로젝트", "kind": "folder"}).get_json()["page"]
    res = _import(auth_client, [("a.md", b"# A"), ("b.md", b"B")], parent_id=project["id"])
    pages = res.get_json()["pages"]
    assert [p["title"] for p in pages] == ["a", "b"]
    assert all(p["parent_id"] == project["id"] for p in pages)
    assert res.get_json()["redirect"] == project["url"]
    # 노트에 떨어뜨리면 그 노트가 있는 폴더로
    res = _import(auth_client, [("c.md", b"C")], parent_id=pages[0]["id"])
    assert res.get_json()["pages"][0]["parent_id"] == project["id"]


def test_zip_roundtrip_keeps_folders_links_and_images(app, auth_client):
    project = auth_client.post("/api/pages", json={"title": "프로젝트", "kind": "folder"}).get_json()["page"]
    sub = auth_client.post("/api/pages", json={"title": "설계", "parent_id": project["id"], "kind": "folder"}).get_json()["page"]
    note = auth_client.post("/api/pages", json={"title": "API", "parent_id": sub["id"]}).get_json()["page"]
    other = auth_client.post("/api/pages", json={"title": "목차", "parent_id": project["id"]}).get_json()["page"]
    upload = auth_client.post(
        "/api/uploads", data={"file": (io.BytesIO(_png()), "그림.png")}, content_type="multipart/form-data",
    ).get_json()
    auth_client.put(f"/api/pages/{note['id']}/source", json={"markdown": f"소개\n\n![그림]({upload['url']})\n\n[목차](../목차.md)"})
    auth_client.put(f"/api/pages/{other['id']}/source", json={"markdown": "[API](설계/API.md)"})

    res = auth_client.get(f"/export/page/{project['id']}.zip")
    assert res.status_code == 200
    archive = res.get_data()
    zf = zipfile.ZipFile(io.BytesIO(archive))
    names = set(zf.namelist())
    assert {"프로젝트/", "프로젝트/설계/", "프로젝트/설계/API.md", "프로젝트/목차.md"} <= names
    image = next(n for n in names if n.startswith("images/"))
    assert f"![그림](../../{image})" in zf.read("프로젝트/설계/API.md").decode()

    # 다시 가져오기 — 같은 이름이 있으므로 먼저 경고(409), 이름 바꿔서 가져오기
    res = _import(auth_client, [("backup.zip", archive)])
    assert res.status_code == 409 and res.get_json()["conflicts"][0]["existing_id"] == project["id"]
    res = _import(auth_client, [("backup.zip", archive)], on_conflict="rename")
    assert res.status_code == 201
    (root,) = res.get_json()["pages"]
    assert root["title"] == "프로젝트 (2)"

    kids = _children(app, root["id"])
    new_api = _children(app, kids["설계"])["API"]
    blocks = _source(auth_client, new_api).split("\n\n")
    assert blocks[0] == "소개"
    assert blocks[1].startswith("![그림](/files/") and upload["url"] not in blocks[1]
    assert blocks[2] == "[목차](../목차.md)"  # 상대 링크 그대로
    assert _source(auth_client, kids["목차"]) == "[API](설계/API.md)"
    served = auth_client.get(blocks[1][len("![그림]("):-1])
    assert served.status_code == 200
    served.close()


def test_import_notion_style_zip(app, auth_client):
    home = "Home 0123456789abcdef0123456789abcdef"
    archive = _zip({
        f"{home}.md": f"# Home\n\n[Child](Home%200123456789abcdef0123456789abcdef/Child%20fedcba9876543210fedcba9876543210.md)\n\n![](Home%200123456789abcdef0123456789abcdef/pic.png)",
        f"{home}/Child fedcba9876543210fedcba9876543210.md": "# Child\n\n내용 [위로](../Home%200123456789abcdef0123456789abcdef.md)",
        f"{home}/Orphan.md": "링크 안 된 노트",
        f"{home}/pic.png": _png(),
    })
    outer = _zip({"Export-123/Part-1.zip": archive})  # 노션처럼 zip 안의 zip
    res = _import(auth_client, [("Export.zip", outer)])
    assert res.status_code == 201
    root = res.get_json()["pages"][0]
    assert root["title"] == "Export-123"  # 바깥 폴더
    home_id = _children(app, root["id"])["Home"]  # Home.md + Home/ → 폴더 Home + 그 안 노트 Home.md
    assert _children(app, home_id).keys() == {"Home", "Child", "Orphan"}
    from app.services import page_tree as tree
    from app.services import store

    with app.app_context():
        readme = tree.name_conflict(home_id, "Home", kind="note")
        child = tree.name_conflict(home_id, "Child")
        readme_src, child_src = tree.page_source(readme), tree.page_source(child)
    blocks = readme_src.split("\n\n")
    assert blocks[0] == "[Child](Child.md)"  # 노션 번호 빠진 새 이름으로
    assert blocks[1].startswith("![](/files/")
    assert child_src == "내용 [위로](Home.md)"  # Home.md 는 폴더 Home 안 노트 Home.md


def test_import_inline_link_to_other_document(app, auth_client):
    res = _import(auth_client, [("a.md", "[B 참고](b%20x.md) 하세요".encode()), ("b x.md", b"B")])
    pages = {p["title"]: p for p in res.get_json()["pages"]}
    assert _source(auth_client, pages["a"]["id"]) == "[B 참고](b%20x.md) 하세요"
    html = auth_client.get(f"/api/pages/{pages['a']['id']}/blocks").get_json()["blocks"][0]["html"]
    assert f'href="{pages["b x"]["url"]}"' in html


def test_import_rejects_unknown_types(auth_client):
    res = _import(auth_client, [("x.exe", b"MZ")])
    assert res.status_code == 400
    assert "지원하지 않는" in res.get_json()["error"]


def test_import_rejects_zip_without_markdown(auth_client):
    res = _import(auth_client, [("x.zip", _zip({"a.png": _png()}))])
    assert res.status_code == 400


def test_export_all(auth_client):
    auth_client.post("/api/pages", json={"title": "하나", "kind": "folder"})
    auth_client.post("/api/pages", json={"title": "둘", "kind": "folder"})
    res = auth_client.get("/export/all.zip")
    names = zipfile.ZipFile(io.BytesIO(res.get_data())).namelist()
    assert sorted(names) == ["둘/", "하나/"]  # 폴더 구조 그대로


# ---------- 같은 이름 ----------

def test_import_conflict_overwrite_moves_old_to_trash(app, auth_client):
    parent = auth_client.post("/api/pages", json={"title": "프로젝트", "kind": "folder"}).get_json()["page"]
    old = auth_client.post("/api/pages", json={"title": "회의록", "parent_id": parent["id"]}).get_json()["page"]
    auth_client.post("/api/pages", json={"title": "다른 노트", "parent_id": parent["id"]})

    doc = [("회의록.md", "# 회의록\n\n새 내용".encode())]
    res = _import(auth_client, doc, parent_id=parent["id"])
    assert res.status_code == 409
    from app.services import store

    def children():
        with app.app_context():
            return [(p.id, p.title) for p in store.children(parent["id"], "active")]
    assert len(children()) == 2  # 아무것도 만들지 않음

    res = _import(auth_client, doc, parent_id=parent["id"], on_conflict="overwrite")
    new = res.get_json()["pages"][0]
    assert new["title"] == "회의록"
    ids = [i for i, _ in children()]
    assert ids[0] == new["id"] and len(ids) == 2  # 기존 자리에 그대로
    assert auth_client.get(f"/api/pages/{old['id']}/blocks").status_code == 200
    with app.app_context():
        assert store.get(old["id"]).status == "trashed"  # 휴지통에서 복원 가능


def test_duplicate_names_in_same_folder(auth_client):
    a = auth_client.post("/api/pages", json={"title": "개발", "kind": "folder"}).get_json()["page"]
    assert auth_client.post("/api/pages", json={"title": " 개발 ", "kind": "folder"}).status_code == 409  # 프로젝트끼리
    b = auth_client.post("/api/pages", json={"title": "디자인", "kind": "folder"}).get_json()["page"]
    note = auth_client.post("/api/pages", json={"title": "회의록", "parent_id": a["id"]}).get_json()["page"]
    assert auth_client.post("/api/pages", json={"title": "회의록", "parent_id": a["id"]}).status_code == 409
    assert auth_client.post("/api/pages", json={"title": "회의록", "parent_id": b["id"]}).status_code == 201  # 다른 폴더는 됨
    assert auth_client.post("/api/pages", json={}).status_code == 201  # 제목 없음은 여러 개 가능
    assert auth_client.post("/api/pages", json={}).status_code == 201

    other = auth_client.post("/api/pages", json={"title": "메모", "parent_id": a["id"]}).get_json()["page"]
    assert auth_client.patch(f"/api/pages/{other['id']}", json={"title": "회의록", "kind": "folder"}).status_code == 409  # 이름 바꾸기
    assert auth_client.patch(f"/api/pages/{note['id']}", json={"parent_id": b["id"]}).status_code == 409  # 옮기기

    # 휴지통에 있는 사이 같은 이름이 생기면 "(2)" 로 복원
    auth_client.post(f"/api/pages/{note['id']}/trash")
    auth_client.post("/api/pages", json={"title": "회의록", "parent_id": a["id"]})
    restored = auth_client.post(f"/api/pages/{note['id']}/restore").get_json()["page"]
    assert restored["title"] == "회의록 (2)"
