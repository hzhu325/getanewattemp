"""v2 功能测试：备份、库存导入导出、回复延迟、客户画像。"""

import pytest
from fastapi.testclient import TestClient

from partspilot import db
from partspilot.api.app import create_app
from partspilot.config import Config
from partspilot.services import backup
from partspilot.services.pipeline import reply_delay_seconds


@pytest.fixture
def client(tmp_path):
    config = Config()
    config.data_dir = tmp_path
    config.admin_password = ""
    config.clawbot_enabled = False
    config.jisu_vin_appkey = ""
    config.tianapi_key = ""
    config.seventeen_vin_user = ""
    with TestClient(create_app(config)) as c:
        c.put("/api/settings", json={"quiet_start": "", "quiet_end": ""})  # 测试与时钟解耦
        yield c


class TestBackup:
    def test_create_list_prune(self, tmp_path):
        db_path = tmp_path / "a.db"
        db.init_db(db_path)
        backup_dir = tmp_path / "backups"

        assert not backup.has_backup_today(backup_dir)
        path = backup.create_backup(db_path, backup_dir)
        assert path.exists() and path.stat().st_size > 0
        assert backup.has_backup_today(backup_dir)
        assert backup.list_backups(backup_dir)[0]["name"] == path.name

        for _ in range(5):
            backup.create_backup(db_path, backup_dir)
        backup.prune_backups(backup_dir, keep=3)
        assert len(backup.list_backups(backup_dir)) == 3

    def test_backup_endpoints(self, client):
        result = client.post("/api/backup").json()
        assert result["ok"] and result["size"] > 0
        listed = client.get("/api/backup/list").json()
        assert len(listed["backups"]) >= 1
        download = client.get("/api/backup/download")
        assert download.status_code == 200
        assert len(download.content) > 0


CSV_OK = (
    "品类,内部编号,名称,品牌,车型,年份,排量,发动机型号,变速箱型号,成色,参考价,状态,备注\n"
    "发动机,E100,凯美瑞2.5发动机,丰田,凯美瑞,2019,2.5L,6AR-FSE,,9成新,9200,在售,\n"
    "变速箱,G100,轩逸CVT,日产,轩逸,2017,1.6L,,RE0F11A,,4300,在售,\n"
)


class TestInventoryCsv:
    def test_template_and_export_have_bom(self, client):
        template = client.get("/api/inventory/template")
        assert template.status_code == 200
        assert template.text.startswith("﻿")
        assert "内部编号" in template.text

    def test_import_create_then_update(self, client):
        r1 = client.post("/api/inventory/import", json={"csv_text": CSV_OK}).json()
        assert (r1["created"], r1["updated"], r1["errors"]) == (2, 0, [])

        # 再导一次同编号 → 更新
        r2 = client.post("/api/inventory/import", json={"csv_text": CSV_OK.replace("9200", "8800")}).json()
        assert (r2["created"], r2["updated"]) == (0, 2)
        items = client.get("/api/inventory?q=E100").json()
        assert items[0]["price"] == 8800

        # 导出应包含两条 + BOM
        export = client.get("/api/inventory/export")
        assert "E100" in export.text and "G100" in export.text

    def test_import_error_rows_reported(self, client):
        bad = (
            "品类,内部编号,名称,参考价\n"
            "飞机,X1,不存在的品类,100\n"
            ",X2,缺编号没关系名称在,100\n"
            "发动机,,缺编号,100\n"
            "发动机,X4,价格不是数,一万二\n"
            "发动机,X5,这行是好的,5000\n"
        )
        r = client.post("/api/inventory/import", json={"csv_text": bad}).json()
        assert r["created"] == 2  # X2（品类留空默认发动机）和 X5
        assert len(r["errors"]) == 3

    def test_import_requires_headers(self, client):
        r = client.post("/api/inventory/import", json={"csv_text": "a,b,c\n1,2,3\n"})
        assert r.status_code == 400


class TestReplyDelay:
    def test_disabled_when_max_zero(self):
        assert reply_delay_seconds({"reply_delay_min": "2", "reply_delay_max": "0"}) == 0

    def test_range(self):
        for _ in range(20):
            d = reply_delay_seconds({"reply_delay_min": "2", "reply_delay_max": "6"})
            assert 2 <= d <= 6

    def test_min_greater_than_max_clamped(self):
        d = reply_delay_seconds({"reply_delay_min": "9", "reply_delay_max": "3"})
        assert 0 <= d <= 3

    def test_garbage_settings(self):
        assert reply_delay_seconds({"reply_delay_min": "x", "reply_delay_max": "y"}) == 0


class TestCustomerStats:
    def test_stats_after_inquiry_and_close(self, client):
        client.post("/api/simulator/message", json={"name": "回头客", "text": "要个迈腾发动机多少钱"})
        cid = client.get("/api/conversations").json()[0]["id"]

        detail = client.get(f"/api/conversations/{cid}/messages").json()
        assert detail["customer_stats"]["inquiry_count"] == 1
        assert detail["customer_stats"]["closed_count"] == 0

        inquiry_id = client.get("/api/inquiries").json()[0]["id"]
        client.post(f"/api/inquiries/{inquiry_id}/status", json={"status": "closed"})

        detail = client.get(f"/api/conversations/{cid}/messages").json()
        stats = detail["customer_stats"]
        assert stats["closed_count"] == 1
        assert "迈腾" in stats["last_closed"]
