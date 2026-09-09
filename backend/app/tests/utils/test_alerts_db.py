import asyncio
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone

from app.utils.database import DatabaseManager
from app.models.alerts import Alert, AlertSeverityEnum


def _manager(path):
    return DatabaseManager({"database": {"db_path": path}})


def _alert():
    return Alert(
        id=0,
        timestamp=datetime.now(timezone.utc),
        severity=AlertSeverityEnum.WARNING,
        feed_id="F1",
        message="m",
        acknowledged=False,
    )


def _run(coro):
    return asyncio.run(coro)


class TestAlertDbMethods(unittest.TestCase):
    def test_ack_get_delete_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = _manager(os.path.join(tmp, "t.db"))
            log_id = _run(asyncio.to_thread(db._execute_save_alert,
                "INSERT INTO alerts (timestamp, severity, feed_id, message, acknowledged)"
                " VALUES (?, ?, ?, ?, ?)",
                (datetime.now(timezone.utc).timestamp(), "WARNING", "F1", "m", 0)))
            _ = log_id
            row = _run(db.get_alert_by_id(1))
            self.assertIsNotNone(row)
            self.assertEqual(row["message"], "m")
            self.assertEqual(row["acknowledged"], 0)
            self.assertTrue(_run(db.acknowledge_alert(1, True)))
            row = _run(db.get_alert_by_id(1))
            self.assertEqual(row["acknowledged"], 1)
            self.assertIsNotNone(row["acknowledged_at"])
            self.assertTrue(_run(db.acknowledge_alert(1, False)))
            self.assertEqual(_run(db.get_alert_by_id(1))["acknowledged"], 0)
            self.assertFalse(_run(db.acknowledge_alert(999, True)))
            self.assertIsNone(_run(db.get_alert_by_id(999)))
            self.assertTrue(_run(db.delete_alert(1)))
            self.assertIsNone(_run(db.get_alert_by_id(1)))
            self.assertFalse(_run(db.delete_alert(1)))

    def test_save_alert_model_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = _manager(os.path.join(tmp, "t.db"))
            _run(db.save_alert(_alert()))
            row = _run(db.get_alert_by_id(1))
            self.assertIsNotNone(row)
            self.assertEqual(row["severity"], "WARNING")
            self.assertEqual(row["feed_id"], "F1")

    def test_migration_widens_legacy_narrow_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "legacy.db")
            conn = sqlite3.connect(path)
            conn.execute("""CREATE TABLE alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                severity TEXT NOT NULL CHECK(severity IN ('INFO', 'WARNING', 'CRITICAL', 'ERROR')),
                feed_id TEXT,
                message TEXT NOT NULL, details TEXT,
                acknowledged INTEGER DEFAULT 0 NOT NULL CHECK(acknowledged IN (0, 1)))""")
            conn.execute(
                "INSERT INTO alerts (timestamp, severity, feed_id, message, acknowledged)"
                " VALUES (?, ?, ?, ?, ?)", (1700000000.0, "WARNING", "F9", "legacy", 0))
            conn.commit()
            conn.close()
            db = _manager(path)
            row = _run(db.get_alert_by_id(1))
            self.assertIsNotNone(row)
            self.assertEqual(row["message"], "legacy")
            self.assertTrue(_run(db.acknowledge_alert(1, True)))
            self.assertEqual(_run(db.get_alert_by_id(1))["acknowledged"], 1)


if __name__ == "__main__":
    unittest.main()
