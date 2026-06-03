"""把 OData V2 $filter 表达式翻译为 pandas DataFrame 过滤。

支持的 OData 操作 (§8.6):
  比较:   eq, ne, gt, ge, lt, le
  逻辑:   and, or, not
  函数:   substringof('x', col), startswith(col, 'x'), endswith(col, 'x')
          tolower(col), toupper(col)
  字面量: 'string', 数字, datetime'2026-05-01T00:00:00', true/false, null
  分组:   括号

不支持:
  $expand 路径里的 filter
  any() / all() lambda 表达式（OData V4）
  自定义函数

实现：递归下降 parser → AST → pandas.DataFrame.query 友好的表达式。
为避免 query() 对引号和函数的限制，部分场景直接生成布尔 Series。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


class FilterParseError(ValueError):
    """$filter 表达式语法错误。"""


# ============================== 词法分析 ==============================


@dataclass
class Token:
    kind: str       # 'IDENT' | 'STRING' | 'NUMBER' | 'DATETIME' | 'OP' | 'LPAREN' | 'RPAREN' | 'COMMA' | 'BOOL' | 'NULL'
    value: Any
    pos: int


# OData 二元运算符，按优先级
_BINARY_OPS = {"or": 1, "and": 2, "eq": 3, "ne": 3, "gt": 3, "ge": 3, "lt": 3, "le": 3}
_UNARY_OPS = {"not"}
_FUNCTIONS = {"substringof", "startswith", "endswith", "tolower", "toupper", "length", "trim"}


def tokenize(expr: str) -> list[Token]:
    tokens: list[Token] = []
    i, n = 0, len(expr)
    while i < n:
        c = expr[i]
        if c.isspace():
            i += 1
            continue
        if c == "(":
            tokens.append(Token("LPAREN", "(", i))
            i += 1
            continue
        if c == ")":
            tokens.append(Token("RPAREN", ")", i))
            i += 1
            continue
        if c == ",":
            tokens.append(Token("COMMA", ",", i))
            i += 1
            continue
        if c == "'":
            # 字符串字面量, OData 用 '' 转义单引号
            j = i + 1
            buf: list[str] = []
            while j < n:
                if expr[j] == "'":
                    if j + 1 < n and expr[j + 1] == "'":
                        buf.append("'")
                        j += 2
                        continue
                    break
                buf.append(expr[j])
                j += 1
            if j >= n:
                raise FilterParseError(f"字符串字面量未闭合 @ {i}")
            tokens.append(Token("STRING", "".join(buf), i))
            i = j + 1
            continue
        if c.isdigit() or (c == "-" and i + 1 < n and expr[i + 1].isdigit()):
            j = i + 1
            while j < n and (expr[j].isdigit() or expr[j] == "."):
                j += 1
            raw = expr[i:j]
            tokens.append(Token("NUMBER", float(raw) if "." in raw else int(raw), i))
            i = j
            continue
        if c.isalpha() or c == "_":
            j = i + 1
            while j < n and (expr[j].isalnum() or expr[j] == "_" or expr[j] == "/"):
                j += 1
            word = expr[i:j]
            lower = word.lower()
            # datetime'...' 形式
            if lower == "datetime" and j < n and expr[j] == "'":
                end = expr.find("'", j + 1)
                if end < 0:
                    raise FilterParseError(f"datetime 字面量未闭合 @ {i}")
                tokens.append(Token("DATETIME", expr[j + 1:end], i))
                i = end + 1
                continue
            if lower in _BINARY_OPS or lower in _UNARY_OPS:
                tokens.append(Token("OP", lower, i))
            elif lower in ("true", "false"):
                tokens.append(Token("BOOL", lower == "true", i))
            elif lower == "null":
                tokens.append(Token("NULL", None, i))
            elif lower in _FUNCTIONS:
                tokens.append(Token("FUNC", lower, i))
            else:
                tokens.append(Token("IDENT", word, i))
            i = j
            continue
        raise FilterParseError(f"无法识别的字符 {c!r} @ {i}")
    return tokens


# ============================== 语法分析 ==============================


@dataclass
class Node:
    kind: str           # 'binop' | 'unop' | 'func' | 'ident' | 'const'
    value: Any
    children: list["Node"]


class Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens = tokens
        self.pos = 0

    def _peek(self) -> Token | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _eat(self) -> Token:
        if self.pos >= len(self.tokens):
            raise FilterParseError("意外的表达式结束")
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def parse(self) -> Node:
        node = self._parse_expr(0)
        if self.pos < len(self.tokens):
            tok = self.tokens[self.pos]
            raise FilterParseError(f"多余的 token {tok.value!r} @ {tok.pos}")
        return node

    def _parse_expr(self, min_prec: int) -> Node:
        left = self._parse_atom()
        while True:
            tok = self._peek()
            if tok is None or tok.kind != "OP" or tok.value not in _BINARY_OPS:
                break
            prec = _BINARY_OPS[tok.value]
            if prec < min_prec:
                break
            self._eat()
            right = self._parse_expr(prec + 1)
            left = Node("binop", tok.value, [left, right])
        return left

    def _parse_atom(self) -> Node:
        tok = self._eat()
        if tok.kind == "LPAREN":
            inner = self._parse_expr(0)
            close = self._eat()
            if close.kind != "RPAREN":
                raise FilterParseError(f"括号未闭合 @ {tok.pos}")
            return inner
        if tok.kind == "OP" and tok.value in _UNARY_OPS:
            child = self._parse_atom()
            return Node("unop", tok.value, [child])
        if tok.kind == "FUNC":
            return self._parse_func_call(tok.value)
        if tok.kind == "IDENT":
            return Node("ident", tok.value, [])
        if tok.kind in ("STRING", "NUMBER", "BOOL", "NULL", "DATETIME"):
            return Node("const", (tok.kind, tok.value), [])
        raise FilterParseError(f"意外 token {tok.kind}={tok.value!r} @ {tok.pos}")

    def _parse_func_call(self, name: str) -> Node:
        lp = self._eat()
        if lp.kind != "LPAREN":
            raise FilterParseError(f"函数 {name} 之后应为左括号 @ {lp.pos}")
        args: list[Node] = []
        if self._peek() and self._peek().kind != "RPAREN":  # type: ignore[union-attr]
            args.append(self._parse_expr(0))
            while self._peek() and self._peek().kind == "COMMA":  # type: ignore[union-attr]
                self._eat()
                args.append(self._parse_expr(0))
        rp = self._eat()
        if rp.kind != "RPAREN":
            raise FilterParseError(f"函数 {name} 之后应有右括号 @ {rp.pos}")
        return Node("func", name, args)


# ============================== 求值（直接生成 pandas.Series 布尔向量） ==============================


def _eval(node: Node, df: pd.DataFrame) -> Any:
    if node.kind == "ident":
        col = node.value
        if col not in df.columns:
            raise FilterParseError(f"字段不存在: {col}")
        return df[col]
    if node.kind == "const":
        kind, val = node.value
        if kind == "DATETIME":
            return pd.Timestamp(val)
        return val
    if node.kind == "unop":
        if node.value == "not":
            return ~_eval(node.children[0], df).astype(bool)
        raise FilterParseError(f"未知 unop: {node.value}")
    if node.kind == "binop":
        left = _eval(node.children[0], df)
        right = _eval(node.children[1], df)
        op = node.value
        if op == "and":
            return left.astype(bool) & right.astype(bool)
        if op == "or":
            return left.astype(bool) | right.astype(bool)
        if op == "eq":
            return left == right
        if op == "ne":
            return left != right
        if op == "gt":
            return left > right
        if op == "ge":
            return left >= right
        if op == "lt":
            return left < right
        if op == "le":
            return left <= right
        raise FilterParseError(f"未知 binop: {op}")
    if node.kind == "func":
        name = node.value
        args = [_eval(c, df) for c in node.children]
        if name == "substringof":
            # OData: substringof('value', col)
            needle, hay = args[0], args[1]
            return hay.astype(str).str.contains(str(needle), regex=False, na=False)
        if name == "startswith":
            col, needle = args[0], args[1]
            return col.astype(str).str.startswith(str(needle), na=False)
        if name == "endswith":
            col, needle = args[0], args[1]
            return col.astype(str).str.endswith(str(needle), na=False)
        if name == "tolower":
            return args[0].astype(str).str.lower()
        if name == "toupper":
            return args[0].astype(str).str.upper()
        if name == "length":
            return args[0].astype(str).str.len()
        if name == "trim":
            return args[0].astype(str).str.strip()
        raise FilterParseError(f"未知函数: {name}")
    raise FilterParseError(f"未知节点类型: {node.kind}")


def apply_filter(df: pd.DataFrame, filter_expr: str) -> pd.DataFrame:
    """对 DataFrame 应用 OData $filter 表达式，返回筛选后的副本。"""
    if not filter_expr or not filter_expr.strip():
        return df
    tokens = tokenize(filter_expr)
    ast = Parser(tokens).parse()
    mask = _eval(ast, df)
    if not isinstance(mask, pd.Series):
        raise FilterParseError(
            "$filter 必须求值为布尔表达式（如 col eq 'X'），不是单值或字段名"
        )
    return df[mask.astype(bool)].reset_index(drop=True)


def apply_orderby(df: pd.DataFrame, orderby: str) -> pd.DataFrame:
    """解析 OData $orderby（如 'Revenue desc, Region'）并排序。"""
    if not orderby or not orderby.strip():
        return df
    cols: list[str] = []
    asc: list[bool] = []
    for part in orderby.split(","):
        toks = part.strip().split()
        if not toks:
            continue
        col = toks[0]
        if col not in df.columns:
            raise FilterParseError(f"$orderby 字段不存在: {col}")
        cols.append(col)
        if len(toks) > 1 and toks[1].lower() == "desc":
            asc.append(False)
        else:
            asc.append(True)
    if not cols:
        return df
    return df.sort_values(cols, ascending=asc).reset_index(drop=True)


def apply_select(df: pd.DataFrame, select: str) -> pd.DataFrame:
    """OData $select：逗号分隔字段列表。"""
    if not select or not select.strip():
        return df
    cols = [c.strip() for c in select.split(",") if c.strip()]
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise FilterParseError(f"$select 字段不存在: {', '.join(missing)}")
    return df[cols].copy()
