"""Tests for odoo_mcp_server — 只測有實際邏輯、容易藏 bug 的地方."""

import json
from unittest.mock import MagicMock, patch

import pytest
from fastmcp.exceptions import ToolError

from odoo_mcp_server import (
    OdooJsonRpcClient,
    _extract_fields_from_arch,
    _sanitize_error_message,
    _view_fields_cache,
    create_record,
    delete_record,
    execute_method,
    get_view_fields,
    handle_tool_errors,
    read_records,
    search_records,
)

# =============================================================================
# 1. connect() URL 解析 — 分支多，使用者輸入格式千變萬化
# =============================================================================


class TestConnect:
    """測試 OdooJsonRpcClient.connect() 的 URL 解析邏輯."""

    @patch("odoo_mcp_server.odoolib.get_connection")
    def test_http_default_port(self, mock_get_conn):
        """http 沒給 port → 預設 8069."""
        mock_get_conn.return_value = MagicMock()
        OdooJsonRpcClient.connect("http://myserver", "db", "key")
        mock_get_conn.assert_called_once_with(
            hostname="myserver",
            port=8069,
            database="db",
            login="api",
            password="key",
            protocol="json2",
        )

    @patch("odoo_mcp_server.odoolib.get_connection")
    def test_https_default_port(self, mock_get_conn):
        """https 沒給 port → 預設 443，protocol 為 json2s."""
        mock_get_conn.return_value = MagicMock()
        OdooJsonRpcClient.connect("https://myserver", "db", "key")
        mock_get_conn.assert_called_once_with(
            hostname="myserver",
            port=443,
            database="db",
            login="api",
            password="key",
            protocol="json2s",
        )

    @patch("odoo_mcp_server.odoolib.get_connection")
    def test_explicit_port(self, mock_get_conn):
        """有明確給 port → 使用指定的 port."""
        mock_get_conn.return_value = MagicMock()
        OdooJsonRpcClient.connect("http://myserver:8080", "db", "key")
        mock_get_conn.assert_called_once_with(
            hostname="myserver",
            port=8080,
            database="db",
            login="api",
            password="key",
            protocol="json2",
        )

    @patch("odoo_mcp_server.odoolib.get_connection")
    def test_trailing_slash_stripped(self, mock_get_conn):
        """URL 尾巴的 / 不應影響 hostname."""
        mock_get_conn.return_value = MagicMock()
        OdooJsonRpcClient.connect("http://myserver:8069/", "db", "key")
        mock_get_conn.assert_called_once_with(
            hostname="myserver",
            port=8069,
            database="db",
            login="api",
            password="key",
            protocol="json2",
        )


# =============================================================================
# 2. read() dict → list 正規化 — odoolib 回傳不一致的防禦邏輯
# =============================================================================


class TestReadNormalization:
    """測試 read() 一定回傳 list，不管 odoolib 回 dict 還是 list."""

    def _make_client(self, read_return_value):
        mock_proxy = MagicMock()
        mock_proxy.read.return_value = read_return_value
        client = OdooJsonRpcClient(connection=MagicMock())
        return client, mock_proxy

    def test_single_record_returns_dict_normalized_to_list(self):
        """odoolib 傳單筆回 dict → 應正規化為 list."""
        client, mock_proxy = self._make_client({"id": 1, "name": "Alice"})
        with patch.object(client, "get_model", return_value=mock_proxy):
            result = client.read("res.partner", [1], fields=["name"])
        assert isinstance(result, list)
        assert result == [{"id": 1, "name": "Alice"}]

    def test_multiple_records_returns_list_unchanged(self):
        """odoolib 傳多筆回 list → 原樣回傳."""
        data = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
        client, mock_proxy = self._make_client(data)
        with patch.object(client, "get_model", return_value=mock_proxy):
            result = client.read("res.partner", [1, 2], fields=["name"])
        assert result == data

    def test_read_without_fields(self):
        """不傳 fields → 呼叫 read(ids) 而非 read(ids, fields)."""
        client, mock_proxy = self._make_client([{"id": 1, "name": "Alice"}])
        with patch.object(client, "get_model", return_value=mock_proxy):
            client.read("res.partner", [1])
        mock_proxy.read.assert_called_once_with([1])


# =============================================================================
# 3. create_record() 單筆 vs 批次 — 兩層 isinstance 判斷，邊界情況多
# =============================================================================


class TestCreateRecord:
    """測試 create_record 的單筆/批次分支邏輯."""

    def _make_mock_client(self, create_return):
        mock_client = MagicMock()
        mock_client.create.return_value = create_return
        return mock_client

    def test_single_creation_returns_id(self):
        """單筆建立 → 回傳 {id, success, url}."""
        client = self._make_mock_client(42)
        result = json.loads(create_record(model="res.partner", values={"name": "Test"}, client=client))
        assert result["id"] == 42
        assert result["success"] is True
        assert "url" in result

    def test_batch_creation_returns_ids(self):
        """批次建立 → 回傳 {ids, count, success, urls}."""
        client = self._make_mock_client([10, 11, 12])
        values = [{"name": "A"}, {"name": "B"}, {"name": "C"}]
        result = json.loads(create_record(model="res.partner", values=values, client=client))
        assert result["ids"] == [10, 11, 12]
        assert result["count"] == 3
        assert result["success"] is True
        assert len(result["urls"]) == 3

    def test_batch_creation_single_id_returned(self):
        """批次建立但 odoo 回傳單一 int（而非 list）→ 應包成 list."""
        client = self._make_mock_client(99)
        values = [{"name": "Only"}]
        result = json.loads(create_record(model="res.partner", values=values, client=client))
        assert result["ids"] == [99]
        assert result["count"] == 1


# =============================================================================
# 4. delete_record() confirm 閘門 — 安全機制，壞了就直接刪使用者資料
# =============================================================================


class TestDeleteRecord:
    """測試 delete_record 的 confirm 安全機制."""

    def test_confirm_false_blocks_deletion(self):
        """confirm=False → 不應呼叫 client，回傳錯誤."""
        mock_client = MagicMock()
        result = json.loads(delete_record(model="res.partner", ids=[1, 2], confirm=False, client=mock_client))
        assert result["status"] == "error"
        assert "confirm" in result["error"].lower()
        mock_client.unlink.assert_not_called()

    def test_confirm_true_executes_deletion(self):
        """confirm=True → 呼叫 client.unlink."""
        mock_client = MagicMock()
        mock_client.unlink.return_value = True
        result = json.loads(delete_record(model="res.partner", ids=[1, 2], confirm=True, client=mock_client))
        assert result["success"] is True
        assert result["deleted_ids"] == [1, 2]
        mock_client.unlink.assert_called_once_with("res.partner", [1, 2])

    def test_default_confirm_is_false(self):
        """不傳 confirm → 預設 False，不執行刪除."""
        mock_client = MagicMock()
        result = json.loads(delete_record(model="res.partner", ids=[1], client=mock_client))
        assert result["status"] == "error"
        mock_client.unlink.assert_not_called()


# =============================================================================
# 5. execute_method + READONLY_MODE — 唯一擋住萬用入口的安全網
# =============================================================================


class TestExecuteMethodReadonly:
    """測試 execute_method 在 READONLY_MODE 下阻擋寫入操作."""

    @pytest.mark.parametrize("method", ["create", "write", "unlink", "copy"])
    def test_readonly_blocks_write_methods(self, monkeypatch, method):
        """READONLY_MODE=True 時，execute_method 應擋住所有寫入方法."""
        monkeypatch.setattr("odoo_mcp_server.READONLY_MODE", True)
        mock_client = MagicMock()

        with pytest.raises(ToolError, match="not allowed"):
            execute_method(model="res.partner", method=method, client=mock_client)
        mock_client.execute.assert_not_called()

    def test_readonly_allows_read_methods(self, monkeypatch):
        """READONLY_MODE=True 時，讀取方法應正常放行."""
        monkeypatch.setattr("odoo_mcp_server.READONLY_MODE", True)
        mock_client = MagicMock()
        mock_client.execute.return_value = [1, 2, 3]

        result = json.loads(execute_method(model="res.partner", method="search", args=[[]], client=mock_client))
        assert result == [1, 2, 3]
        mock_client.execute.assert_called_once()


# =============================================================================
# 6. handle_tool_errors — 統一錯誤處理 decorator
# =============================================================================


class TestHandleToolErrors:
    """測試 handle_tool_errors decorator 的錯誤轉換邏輯."""

    def test_normal_return_passes_through(self):
        """正常回傳值不受影響."""

        @handle_tool_errors
        def good_func():
            return "ok"

        assert good_func() == "ok"

    def test_generic_exception_converted_to_tool_error(self):
        """一般 Exception → 轉為 ToolError，保留原始訊息."""

        @handle_tool_errors
        def bad_func():
            raise RuntimeError("model not found")

        with pytest.raises(ToolError, match="bad_func failed: model not found"):
            bad_func()

    def test_tool_error_not_wrapped(self):
        """已經是 ToolError → 直接 re-raise，不會被包兩層."""

        @handle_tool_errors
        def readonly_func():
            raise ToolError("not allowed in READONLY_MODE")

        with pytest.raises(ToolError, match="not allowed in READONLY_MODE"):
            readonly_func()

    def test_original_exception_chained(self):
        """原始 Exception 應保留在 __cause__ 中."""

        @handle_tool_errors
        def failing_func():
            raise ValueError("bad value")

        with pytest.raises(ToolError) as exc_info:
            failing_func()
        assert isinstance(exc_info.value.__cause__, ValueError)

    def test_search_records_rpc_error(self):
        """實際 tool：search_records RPC 錯誤 → ToolError."""
        mock_client = MagicMock()
        mock_client.search_read.side_effect = Exception("Object res.partnerr doesn't exist")

        with pytest.raises(ToolError, match="search_records failed.*doesn't exist"):
            search_records(model="res.partnerr", client=mock_client)

    def test_execute_method_rpc_error(self):
        """實際 tool：execute_method RPC 錯誤 → ToolError."""
        mock_client = MagicMock()
        mock_client.execute.side_effect = ConnectionError("Connection refused")

        with pytest.raises(ToolError, match="execute_method failed.*Connection refused"):
            execute_method(model="res.partner", method="search", args=[[]], client=mock_client)


# =============================================================================
# 7. _sanitize_error_message — 去除 Odoo debug traceback
# =============================================================================


class TestSanitizeErrorMessage:
    """測試 _sanitize_error_message 的 debug 欄位移除邏輯."""

    def test_strips_debug_from_odoo_rpc_error(self):
        """Odoo RPC 錯誤 → 移除 debug 欄位，保留其他資訊."""
        body = {
            "name": "werkzeug.exceptions.NotFound",
            "message": "the model 'res.partnersa' does not exist",
            "arguments": ["the model 'res.partnersa' does not exist", 404],
            "context": {},
            "debug": "Traceback (most recent call last):\n  File ...\n  ...",
        }
        error = Exception(f"Unexpected status code 404: {json.dumps(body)}")
        result = _sanitize_error_message(error)

        assert "debug" not in result
        assert "Traceback" not in result
        assert "the model 'res.partnersa' does not exist" in result
        assert "Unexpected status code 404:" in result
        assert "werkzeug.exceptions.NotFound" in result

    def test_no_debug_field_unchanged(self):
        """JSON body 沒有 debug 欄位 → 原樣回傳."""
        body = {"name": "SomeError", "message": "something went wrong"}
        error = Exception(f"Status 500: {json.dumps(body)}")
        result = _sanitize_error_message(error)
        assert result == str(error)

    def test_plain_exception_unchanged(self):
        """非 JSON 的一般 Exception → 原樣回傳."""
        error = ConnectionError("Connection refused")
        result = _sanitize_error_message(error)
        assert result == "Connection refused"

    def test_non_dict_json_unchanged(self):
        """JSON 是 array 而非 dict → 原樣回傳."""
        error = Exception("Some prefix: [1, 2, 3]")
        result = _sanitize_error_message(error)
        assert result == str(error)

    def test_integration_with_decorator(self):
        """decorator 整合測試：Odoo 風格錯誤經過 decorator 後 debug 被移除."""
        body = {
            "message": "field 'namee' does not exist",
            "debug": "Traceback ...\n  very long traceback ...",
        }

        @handle_tool_errors
        def failing_tool():
            raise Exception(f"Unexpected status code 400: {json.dumps(body)}")

        with pytest.raises(ToolError) as exc_info:
            failing_tool()
        error_msg = str(exc_info.value)
        assert "field 'namee' does not exist" in error_msg
        assert "Traceback" not in error_msg


# =============================================================================
# 8. VIEW_FILTERED_MODE — View 可見欄位過濾
# =============================================================================


class TestExtractFieldsFromArch:
    """測試從 view arch XML 解析欄位名稱."""

    def test_tree_view(self):
        """tree view 應正確解析所有 field."""
        arch = '<tree><field name="name"/><field name="department_id"/><field name="job_title"/></tree>'
        fields = _extract_fields_from_arch(arch)
        assert fields == {"name", "department_id", "job_title"}

    def test_form_view_nested(self):
        """form view 嵌套結構應遞迴解析."""
        arch = """<form>
            <sheet>
                <group>
                    <field name="name"/>
                    <field name="email"/>
                </group>
                <notebook>
                    <page>
                        <field name="phone"/>
                    </page>
                </notebook>
            </sheet>
        </form>"""
        fields = _extract_fields_from_arch(arch)
        assert fields == {"name", "email", "phone"}

    def test_empty_view(self):
        """空 view 回傳空集合."""
        arch = "<tree></tree>"
        fields = _extract_fields_from_arch(arch)
        assert fields == set()

    def test_invalid_xml(self):
        """無效 XML 不 crash，回傳空集合."""
        fields = _extract_fields_from_arch("not xml at all")
        assert fields == set()

    def test_field_without_name(self):
        """沒有 name 屬性的 field 應忽略."""
        arch = '<tree><field name="name"/><field/></tree>'
        fields = _extract_fields_from_arch(arch)
        assert fields == {"name"}


class TestGetViewFields:
    """測試 get_view_fields() 合併 tree + form view 欄位."""

    def setup_method(self):
        """每個測試前清除快取."""
        _view_fields_cache.clear()

    def _make_mock_client(self, list_fields, form_fields, list_arch=None, form_arch=None):
        """建立 mock client，模擬 fields_view_get 回傳."""
        mock_client = MagicMock()

        def fake_fields_view_get(model, view_type="form", view_id=None):
            if view_type == "list":
                arch = list_arch or "<tree>" + "".join(f'<field name="{f}"/>' for f in list_fields) + "</tree>"
                return {"arch": arch, "fields": {f: {"type": "char"} for f in list_fields}}
            else:
                arch = form_arch or "<form>" + "".join(f'<field name="{f}"/>' for f in form_fields) + "</form>"
                return {"arch": arch, "fields": {f: {"type": "char"} for f in form_fields}}

        mock_client.fields_view_get = fake_fields_view_get
        return mock_client

    def test_merges_tree_and_form(self):
        """tree + form 的欄位應合併."""
        client = self._make_mock_client(
            list_fields=["name", "department_id"],
            form_fields=["name", "email", "phone"],
        )
        result = get_view_fields(client, "hr.employee")
        assert "name" in result
        assert "department_id" in result
        assert "email" in result
        assert "phone" in result
        assert "id" in result  # 永遠包含 id

    def test_cache_hit(self):
        """第二次呼叫應用快取，不再查 view."""
        client = self._make_mock_client(
            list_fields=["name"],
            form_fields=["name", "email"],
        )
        result1 = get_view_fields(client, "hr.employee")
        result2 = get_view_fields(client, "hr.employee")
        assert result1 is result2  # 同一個物件（快取）

    def test_view_error_graceful(self):
        """view 查詢失敗不 crash，回傳至少有 id 的集合."""
        mock_client = MagicMock()
        mock_client.fields_view_get.side_effect = Exception("Access denied")
        result = get_view_fields(mock_client, "hr.employee")
        assert "id" in result


class TestViewFilteredMode:
    """測試 VIEW_FILTERED_MODE 下 search_records 和 read_records 的行為."""

    def setup_method(self):
        _view_fields_cache.clear()

    def test_search_records_filters_fields(self, monkeypatch):
        """VIEW_FILTERED_MODE=True 時，search_records 只回傳 view 可見欄位."""
        monkeypatch.setattr("odoo_mcp_server.VIEW_FILTERED_MODE", True)

        # 模擬 view 只有 name 和 department_id
        _view_fields_cache["hr.employee"] = {"id", "name", "department_id"}

        mock_client = MagicMock()
        mock_client.fields_get.return_value = {
            "id": {"type": "integer"},
            "name": {"type": "char"},
            "department_id": {"type": "many2one"},
        }
        mock_client.search_read.return_value = [
            {"id": 1, "name": "Alice", "department_id": [1, "IT"]},
        ]
        mock_client.search_count.return_value = 1

        result = json.loads(search_records(model="hr.employee", client=mock_client))
        # 確認 search_read 被呼叫時只帶了允許的欄位
        call_kwargs = mock_client.search_read.call_args
        called_fields = call_kwargs.kwargs.get("fields") or call_kwargs[1].get("fields")
        assert set(called_fields).issubset({"id", "name", "department_id"})

    def test_search_records_blocks_extra_fields(self, monkeypatch):
        """VIEW_FILTERED_MODE=True 時，用戶指定的不可見欄位被過濾掉."""
        monkeypatch.setattr("odoo_mcp_server.VIEW_FILTERED_MODE", True)

        _view_fields_cache["hr.employee"] = {"id", "name", "department_id"}

        mock_client = MagicMock()
        mock_client.search_read.return_value = [{"id": 1, "name": "Alice"}]
        mock_client.search_count.return_value = 1

        # 用戶嘗試指定 salary 欄位（不在 view 中）
        search_records(
            model="hr.employee",
            fields=["name", "salary", "private_phone"],
            client=mock_client,
        )
        call_kwargs = mock_client.search_read.call_args
        called_fields = call_kwargs.kwargs.get("fields") or call_kwargs[1].get("fields")
        assert "salary" not in called_fields
        assert "private_phone" not in called_fields
        assert "name" in called_fields

    def test_read_records_filters_fields(self, monkeypatch):
        """VIEW_FILTERED_MODE=True 時，read_records 也受限."""
        monkeypatch.setattr("odoo_mcp_server.VIEW_FILTERED_MODE", True)

        _view_fields_cache["hr.employee"] = {"id", "name"}

        mock_client = MagicMock()
        mock_client.fields_get.return_value = {
            "id": {"type": "integer"},
            "name": {"type": "char"},
        }
        mock_client.read.return_value = [{"id": 1, "name": "Alice"}]

        read_records(model="hr.employee", ids=[1], client=mock_client)
        call_args = mock_client.read.call_args
        called_fields = call_args[0][2] if len(call_args[0]) > 2 else call_args.kwargs.get("fields")
        assert "name" in called_fields

    def test_disabled_returns_all_safe_fields(self, monkeypatch):
        """VIEW_FILTERED_MODE=False 時，回傳所有安全欄位（原行為）."""
        monkeypatch.setattr("odoo_mcp_server.VIEW_FILTERED_MODE", False)

        mock_client = MagicMock()
        mock_client.fields_get.return_value = {
            "name": {"type": "char"},
            "salary": {"type": "float"},
            "photo": {"type": "binary"},  # 應被排除
        }
        mock_client.search_read.return_value = []
        mock_client.search_count.return_value = 0

        search_records(model="hr.employee", client=mock_client)
        call_kwargs = mock_client.search_read.call_args
        called_fields = call_kwargs.kwargs.get("fields") or call_kwargs[1].get("fields")
        assert "name" in called_fields
        assert "salary" in called_fields
        assert "photo" not in called_fields  # binary 被排除
