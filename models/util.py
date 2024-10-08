from flask import current_app, abort
from typing import Callable, Optional, TypeVar, List
from dataclasses import fields, is_dataclass
from collections import defaultdict
import copy
from dataclasses import dataclass


def transact(transact: str, params=None, have_return=True):
    pool = current_app.extensions["pool"]
    return pool(transact, params, have_return)


T = TypeVar("T")


def unique(func: Callable[..., Optional[List[T]]]) -> Callable[..., Optional[T]]:
    def wrapper(*args, **kwargs):
        res = func(*args, **kwargs)
        return res[0] if res else None

    return wrapper


def from_sql_results(cls, results):
    title, content = results
    return [cls(**dict(zip(title, item))) for item in content]


def exists(cls, use_and_operator=True, **kwargs):
    op = "AND" if use_and_operator else "OR"
    _, val = transact(
        f"SELECT 1 WHERE EXISTS(SELECT 1 FROM {cls.__name__} WHERE {f' {op} '.join([f'{key} = %s' for key in kwargs.keys()])})",
        tuple(kwargs.values()),
    )
    return bool(val)


def get_config(key, default=None):
    return current_app.config.get(key, default)


def get_extension(key, default=None):
    return current_app.extensions.get(key, default)


def field_names(cls):
    if is_dataclass(cls):
        return [field.name for field in fields(cls)]
    else:
        raise ValueError("Not a dataclass")


@dataclass
class ModelBase:
    id: int

    @classmethod
    def sql_generator(cls):
        return SQLBuilder().table(cls.__name__)

    @classmethod
    def delete_by_id(cls, id):
        transact(
            cls.sql_generator().delete().where("id = %s").build(),
            params=(id,),
            have_return=False,
        )

    @classmethod
    @unique
    def get_by_id(cls, id):
        return from_sql_results(
            cls,
            transact(
                cls.sql_generator()
                .select()
                .col(*field_names(cls))
                .where("id = %s")
                .build(),
                params=(id,),
            ),
        )

    def reload_status(self):
        new_self = self.__class__.get_by_id(self.id)
        if new_self:
            for key, val in new_self.__dict__.items():
                setattr(self, key, val)
        else:
            abort(404)


class SQLBuilder:
    __slots__ = (
        "_where",
        "_having",
        "_value",
        "_query_type",
        "_col",
        "_join",
        "_table",
        "_group_by",
        "_order_by",
        "_offset",
        "_fetch",
    )

    def __init__(self):
        self._where = []
        self._having = []
        self._value = []
        self._col = None
        self._join = defaultdict(list)
        self._query_type = None
        self._table = None
        self._group_by = None
        self._order_by = None
        self._offset = None
        self._fetch = None

    def __add__(self, other):
        if not isinstance(other, SQLBuilder):
            raise ValueError("Unsupported Type")

        res = SQLBuilder()
        for k in other.__slots__:
            v = getattr(other, k)
            if not v:
                setattr(res, k, getattr(self, k))
            elif isinstance(v, list):
                setattr(res, k, getattr(self, k) + v)
            else:
                nv = getattr(self, k)
                setattr(res, k, nv if nv else v)
        return res

    def __iadd__(self, other):
        if not isinstance(other, SQLBuilder):
            raise ValueError("Unsupported Type")

        for k in other.__slots__:
            v = getattr(other, k)
            if not v:
                continue
            if isinstance(v, list):
                getattr(self, k).extend(v)
            elif not getattr(self, k):
                setattr(self, k, v)
        return self

    def _get_table_name(self, table):
        if isinstance(table, str):
            return table
        elif isinstance(table, type) and issubclass(table, ModelBase):
            return table.__name__
        else:
            raise ValueError("Argument Table with a invalid value")

    def value(self, *val):
        self._value.extend(list(val))
        return self

    def insert(self):
        self._query_type = "INSERT"
        return self

    def delete(self):
        self._query_type = "DELETE"
        return self

    def update(self):
        self._query_type = "UPDATE"
        return self

    def select(self):
        self._query_type = "SELECT"
        return self

    def col(self, *columns):
        self._col = list(columns)
        return self

    def table(self, table):
        self._table = self._get_table_name(table)
        return self

    def join(self, table, *condition):
        self._join[self._get_table_name(table)].extend(list(condition))
        return self

    def where(self, *condition):
        self._where.extend(list(condition))
        return self

    def group_by(self, *columns):
        self._group_by = ", ".join(columns)
        return self

    def having(self, *condition):
        self._having.extend(list(condition))
        return self

    def order_by(self, **columns_order):
        self._order_by = (
            f"{', '.join(f'{col} {order}' for col, order in columns_order.items())}"
        )
        return self

    def offset(self, offset):
        self._offset = offset
        return self

    def fetch(self, fetch_size):
        self._fetch = fetch_size
        return self

    def copy(self):
        return copy.deepcopy(self)

    def build(self) -> str:
        if self._query_type not in ["SELECT", "INSERT", "UPDATE", "DELETE"]:
            raise ValueError("Unsupported query type")

        if not self._table:
            raise ValueError("Table Name NOT Specified")

        def add_where():
            if self._where:
                return f" WHERE {' AND '.join(self._where)}"
            return ""

        if self._query_type == "DELETE":
            query = f"DELETE FROM {self._table}" + add_where()

        elif not self._col:
            raise ValueError("Columns NOT Specified")

        elif self._query_type == "INSERT":
            query = f"INSERT INTO {self._table}({', '.join(self._col)}) VALUES({', '.join(self._value + ['%s'] * (len(self._col) - len(self._value)))})"

        elif self._query_type == "UPDATE":
            query = (
                f"UPDATE {self._table} SET {', '.join([f'{col}={val}' for col, val in zip(self._col, self._value + ['%s'] * (len(self._col) - len(self._value)))])}"
                + add_where()
            )

        elif self._query_type == "SELECT":
            query = (
                f"SELECT {', '.join(self._col)} FROM {self._table}"
                + "".join(
                    f" INNER JOIN {k} ON {' AND '.join(v)}"
                    for k, v in self._join.items()
                )
                + add_where()
            )
            if self._group_by:
                query += f" GROUP BY {self._group_by}"
                if self._having:
                    query += f" HAVING {' AND '.join(self._having)}"
            if self._order_by:
                query += f" ORDER BY {self._order_by}"
                if self._offset is not None:
                    query += f" OFFSET {self._offset} ROWS"
                    if self._fetch:
                        query += f" FETCH NEXT {self._fetch} ROWS ONLY"

        return query + ";"


class PageIter:
    def __init__(
        self, target_cls: ModelBase, gen: SQLBuilder, page_size, order_by, desc=True
    ):
        self.page = 0
        self.page_size = page_size
        self.target_cls = target_cls
        self.gen = gen.order_by(**{order_by: "DESC" if desc else "ASC"})

    def __iter__(self):
        return self

    def __next__(self):
        sql = self.gen.offset(self.page_size * self.page).fetch(self.page_size).build()
        res = from_sql_results(self.target_cls, transact(sql))
        if not res:
            raise StopIteration
        self.page += 1
        return res
