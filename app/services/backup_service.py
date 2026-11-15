"""Small logical JSON snapshots of this application's prefixes, not native fdbbackup."""
import os
import json
import time
from app.database import get_db, db_manager
from app.database.schema import APP_PREFIXES, get_prefix_range, unpack_key, clear_application_data

class BackupService:
    MAX_BYTES = 1_000_000  # conservative demo transaction bound

    @staticmethod
    def export_backup(target_path):
        @db_manager.transactional
        def read(tr):
            rows = []
            size = 0
            for prefix in APP_PREFIXES:
                for k, v in tr.get_range(*get_prefix_range((prefix,)), limit=10001):
                    size += len(k) + len(v)
                    rows.append({'key_hex': k.hex(), 'val_hex': v.hex()})
                    if size > BackupService.MAX_BYTES or len(rows) > 10000:
                        raise ValueError('Snapshot demo quá lớn; dùng fdbbackup cho dữ liệu lớn')
            return rows
        rows = read(get_db())
        payload = {'metadata': {'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                   'export_version': '2.1', 'total_records': len(rows)}, 'records': rows}
        os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
        with open(target_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return {'file_path': target_path, 'total_records': len(rows), 'size_bytes': os.path.getsize(target_path)}

    @staticmethod
    def restore_backup(source_path):
        with open(source_path, encoding='utf-8') as f:
            payload = json.load(f)
        records = payload.get('records')
        if not isinstance(records, list) or len(records) > 10000:
            raise ValueError('records phải là danh sách tối đa 10000 bản ghi')
        rows, seen, size = [], set(), 0
        for rec in records:
            k, v = bytes.fromhex(rec['key_hex']), bytes.fromhex(rec['val_hex'])
            parts = unpack_key(k)
            if not parts or parts[0] not in APP_PREFIXES or k in seen:
                raise ValueError('Khóa trùng hoặc ngoài phạm vi ứng dụng')
            if len(k) > 10000 or len(v) > 100000:
                raise ValueError('Key/value vượt giới hạn')
            seen.add(k)
            size += len(k) + len(v)
            rows.append((k, v))
        if size > BackupService.MAX_BYTES:
            raise ValueError('Snapshot demo quá lớn')
        @db_manager.transactional
        def restore(tr):
            # Check existing dataset before clearing: also bounds mutation/conflict ranges.
            existing_size = 0
            for prefix in APP_PREFIXES:
                for k, v in tr.get_range(*get_prefix_range((prefix,)), limit=10001):
                    existing_size += len(k) + len(v)
                    if existing_size > BackupService.MAX_BYTES:
                        raise ValueError('Dữ liệu hiện tại quá lớn để restore bằng demo')
            clear_application_data(tr)
            for k, v in rows:
                tr.set(k, v)
        restore(get_db())
        return {'restored_records': len(rows), 'source_metadata': payload.get('metadata', {})}
