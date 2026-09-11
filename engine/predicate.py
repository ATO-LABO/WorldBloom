"""Safe predicate compiler and evaluator for implementation plan §2."""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


class Namespace(dict[str, Any]):
    """Dictionary-like namespace exposed to predicates."""


_PREDICATE_NAMES = {
    "stance",
    "bonds",
    "awareness",
    "holds",
    "holder",
    "zone",
    "present",
    "vitality",
    "known",
    "knows_modifier",
    "strength",
    "believed_strength",
    "hostile_present",
    "turn",
    "day",
    "phase",
    "self",
}


_ALLOWED_COMPARISONS: dict[type[ast.cmpop], str] = {
    ast.Eq: "eq",
    ast.NotEq: "ne",
    ast.Lt: "lt",
    ast.LtE: "le",
    ast.Gt: "gt",
    ast.GtE: "ge",
    ast.In: "in",
    ast.NotIn: "not_in",
}


def _invalid(node: ast.AST) -> ValueError:
    return ValueError(f"Unsupported predicate syntax: {type(node).__name__}")


def _validate(
    node: ast.AST,
    allowed_names: set[str] | None,
) -> None:
    if isinstance(node, ast.Expression):
        _validate(node.body, allowed_names)
        return
    if isinstance(node, ast.BoolOp):
        if not isinstance(node.op, (ast.And, ast.Or)) or len(node.values) < 2:
            raise _invalid(node)
        for value in node.values:
            _validate(value, allowed_names)
        return
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, ast.Not):
            raise _invalid(node)
        _validate(node.operand, allowed_names)
        return
    if isinstance(node, ast.Compare):
        _validate(node.left, allowed_names)
        if not node.comparators or len(node.ops) != len(node.comparators):
            raise _invalid(node)
        for operator in node.ops:
            if type(operator) not in _ALLOWED_COMPARISONS:
                raise _invalid(operator)
        for comparator in node.comparators:
            _validate(comparator, allowed_names)
        return
    if isinstance(node, ast.Name):
        if allowed_names is not None and node.id not in allowed_names:
            raise ValueError(f"Unknown predicate name: {node.id}")
        return
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (str, int, float, bool, type(None))):
            raise _invalid(node)
        return
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.keywords:
            raise _invalid(node)
        if (
            allowed_names is not None
            and node.func.id not in allowed_names
        ):
            raise ValueError(
                f"Unknown predicate function: {node.func.id}"
            )
        for argument in node.args:
            _validate(argument, allowed_names)
        return
    if isinstance(node, (ast.Set, ast.Tuple, ast.List)):
        for element in node.elts:
            if not isinstance(element, ast.Constant):
                raise _invalid(element)
            _validate(element, allowed_names)
        return
    raise _invalid(node)


def _compare(operator: ast.cmpop, left: Any, right: Any) -> bool:
    if isinstance(operator, ast.Eq):
        return left == right
    if isinstance(operator, ast.NotEq):
        return left != right
    if isinstance(operator, ast.Lt):
        return left < right
    if isinstance(operator, ast.LtE):
        return left <= right
    if isinstance(operator, ast.Gt):
        return left > right
    if isinstance(operator, ast.GtE):
        return left >= right
    if isinstance(operator, ast.In):
        return left in right
    if isinstance(operator, ast.NotIn):
        return left not in right
    raise _invalid(operator)


def _evaluate(node: ast.AST, namespace: Mapping[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body, namespace)
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            for value in node.values:
                result = _evaluate(value, namespace)
                if not result:
                    return False
            return True
        for value in node.values:
            result = _evaluate(value, namespace)
            if result:
                return True
        return False
    if isinstance(node, ast.UnaryOp):
        return not bool(_evaluate(node.operand, namespace))
    if isinstance(node, ast.Compare):
        left = _evaluate(node.left, namespace)
        for operator, comparator in zip(node.ops, node.comparators, strict=True):
            right = _evaluate(comparator, namespace)
            if not _compare(operator, left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Name):
        if node.id not in namespace:
            raise ValueError(f"Unknown predicate name: {node.id}")
        return namespace[node.id]
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Call):
        function = namespace.get(node.func.id)
        if not callable(function):
            raise ValueError(f"Unknown predicate function: {node.func.id}")
        arguments = [_evaluate(argument, namespace) for argument in node.args]
        return function(*arguments)
    if isinstance(node, ast.Set):
        return {_evaluate(element, namespace) for element in node.elts}
    if isinstance(node, ast.Tuple):
        return tuple(_evaluate(element, namespace) for element in node.elts)
    if isinstance(node, ast.List):
        return [_evaluate(element, namespace) for element in node.elts]
    raise _invalid(node)


@dataclass(frozen=True)
class Predicate:
    """A syntax-checked predicate expression."""

    source: str
    tree: ast.Expression

    def evaluate(self, ns: Namespace) -> bool:
        return bool(_evaluate(self.tree, ns))


def _parse_predicate(src: str) -> ast.Expression:
    """Parse a predicate and reject syntax outside the whitelist."""

    if not isinstance(src, str) or not src.strip():
        raise ValueError("Predicate source must be a non-empty string")
    try:
        parsed = ast.parse(src, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Invalid predicate syntax: {src!r}") from exc
    if not isinstance(parsed, ast.Expression):
        raise ValueError("Predicate must be an expression")
    return parsed


def compile_predicate_syntax(src: str) -> Predicate:
    """Compile a predicate while validating syntax but deferring names."""

    parsed = _parse_predicate(src)
    _validate(parsed, None)
    return Predicate(source=src, tree=parsed)


def compile_predicate(
    src: str,
    allowed_names: set[str] | None = None,
) -> Predicate:
    """Compile a predicate with default-deny name validation."""

    parsed = _parse_predicate(src)
    names = (
        set(_PREDICATE_NAMES)
        if allowed_names is None
        else set(allowed_names)
    )
    _validate(parsed, names)
    return Predicate(source=src, tree=parsed)
