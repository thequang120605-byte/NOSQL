"""Synchronous service facade; official FDB decorator owns commit and retry."""
from functools import wraps

class RealTransactionAdapter:
    def __init__(self, tr):
        self.tr = tr

    def get(self, key):
        return self.tr.get(key).wait()

    def set(self, key, value):
        self.tr.set(key, value)

    def clear(self, key):
        self.tr.clear(key)

    def clear_range(self, begin, end):
        self.tr.clear_range(begin, end)

    def get_range(self, begin, end, limit=None, reverse=False):
        return [(kv.key, kv.value) for kv in self.tr.get_range(
            begin, end, limit=0 if limit is None else limit, reverse=reverse)]

    def __getitem__(self, key):
        return self.get_range(key.start, key.stop) if isinstance(key, slice) else self.get(key)

    def __setitem__(self, key, value):
        self.set(key, value)

class RealDatabaseAdapter:
    def __init__(self, db):
        self.db = db

    def transactional(self, func):
        import fdb
        @fdb.transactional
        def execute(tr, *args, **kwargs):
            return func(RealTransactionAdapter(tr), *args, **kwargs)
        @wraps(func)
        def wrapper(db_or_tr, *args, **kwargs):
            if isinstance(db_or_tr, RealTransactionAdapter):
                return func(db_or_tr, *args, **kwargs)
            return execute(self.db, *args, **kwargs)
        return wrapper

    def get(self, key):
        return self.transactional(lambda tr: tr.get(key))(self)

    def set(self, key, value):
        return self.transactional(lambda tr: tr.set(key, value))(self)

    def get_range(self, begin, end, limit=None, reverse=False):
        return self.transactional(lambda tr: tr.get_range(begin, end, limit, reverse))(self)
