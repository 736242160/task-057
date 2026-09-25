#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
editor_kernel.py — 单文件「编辑器内核」：版本化编辑操作 + 冲突检测 + 任意回滚 + 原子应用。

功能
----
1. 三种编辑操作：insert(pos, text) / delete(start, end) / replace(start, end, text)。
2. 每个操作携带 base（基于的版本号）。base 不是当前最新版本 => 报冲突（定位到具体操作）。
3. 支持 rollback_to(version) 回滚到任意历史版本；回滚后继续应用新操作，版本链正确延续。
4. 操作位置越界 => 报错并指出是第几个操作、越界位置与当时文本长度。
5. 原子性：一批操作要么全部按序应用成功，要么整体回滚到初始状态，绝不半途而废。

用法
----
    python3 editor_kernel.py            # 运行内置演示（最终文本 / 回滚 / 冲突 / 越界 / 原子性）
    python3 editor_kernel.py ops.json   # 从 JSON 文件读取初始文本与操作序列并执行
    cat ops.json | python3 editor_kernel.py -   # 从标准输入读取

JSON 输入格式
-------------
{
  "initial_text": "hello world",
  "operations": [
    {"type": "insert",  "base": 0, "pos": 5, "text": ","},
    {"type": "delete",  "base": 1, "start": 0, "end": 5},
    {"type": "replace", "base": 2, "start": 0, "end": 5, "text": "WORLD"}
  ],
  "rollback_to": 1,                      # 可选：应用完 operations 后回滚到该版本
  "operations_after_rollback": [ ... ]   # 可选：回滚后继续应用的操作
}

纯 Python 标准库，无第三方依赖。
"""

from __future__ import annotations

import json
import sys


# ---------------------------------------------------------------------------
# 错误类型
# ---------------------------------------------------------------------------

class EditorError(Exception):
    """操作应用失败。携带失败操作的下标、操作内容与原因，便于定位。"""

    def __init__(self, op_index, op, reason):
        self.op_index = op_index
        self.op = op
        self.reason = reason
        super().__init__(
            "操作 #{} 失败: {} | 操作内容: {}".format(op_index, reason, json.dumps(op, ensure_ascii=False))
        )


class ConflictError(EditorError):
    """版本冲突：操作基于的 base 版本不是当前最新版本。"""


class OutOfRangeError(EditorError):
    """位置越界：操作位置超出当前文本长度。"""


# ---------------------------------------------------------------------------
# 编辑器内核
# ---------------------------------------------------------------------------

class EditorKernel:
    """版本化文本编辑器内核。

    - 每个版本保存一份文本快照（snapshots: version -> text）。
    - current 指向当前最新版本。
    - 回滚只是移动 current 指针；回滚后应用新操作会覆盖旧分支上的同名版本号，
      形成一条新的版本链（类似 git reset 后继续提交）。
    """

    def __init__(self, initial_text=""):
        self._initial_text = initial_text
        self._snapshots = {0: initial_text}
        self._current = 0
        self.log = []  # 已应用操作的审计日志: (op_index, op, from_version, to_version)

    # -- 状态查询 ----------------------------------------------------------

    @property
    def text(self):
        return self._snapshots[self._current]

    @property
    def version(self):
        return self._current

    def version_text(self, version):
        if version not in self._snapshots:
            raise KeyError("版本 v{} 不存在（已有版本: {}）".format(version, sorted(self._snapshots)))
        return self._snapshots[version]

    # -- 内部：参数与边界检查 ----------------------------------------------

    def _require_keys(self, op, keys, op_index):
        missing = [k for k in keys if k not in op]
        if missing:
            raise EditorError(op_index, op, "缺少必需字段: {}".format(", ".join(missing)))

    def _check_base(self, op, op_index):
        self._require_keys(op, ["base"], op_index)
        base = op["base"]
        if base != self._current:
            raise ConflictError(
                op_index,
                op,
                "版本冲突：操作基于 v{}，但当前最新版本是 v{}（中间存在其他操作）".format(base, self._current),
            )

    def _check_range(self, op, op_index, start, end):
        n = len(self.text)
        if not (0 <= start <= end <= n):
            raise OutOfRangeError(
                op_index,
                op,
                "位置越界：区间 [{}, {}) 超出当前文本长度 {}（当前文本: {!r}）".format(start, end, n, self.text),
            )

    # -- 核心：应用单个操作 --------------------------------------------------

    def apply(self, op, op_index=0):
        """应用单个操作，成功返回新版本号；失败抛出 EditorError，状态不变。"""
        if not isinstance(op, dict) or "type" not in op:
            raise EditorError(op_index, op, "操作必须是含 'type' 字段的对象")

        self._check_base(op, op_index)  # 先查版本冲突，再查越界
        otype = op["type"]
        text = self.text

        if otype == "insert":
            self._require_keys(op, ["pos", "text"], op_index)
            pos = op["pos"]
            self._check_range(op, op_index, pos, pos)  # pos 允许等于 len（末尾追加）
            new_text = text[:pos] + op["text"] + text[pos:]
        elif otype == "delete":
            self._require_keys(op, ["start", "end"], op_index)
            self._check_range(op, op_index, op["start"], op["end"])
            new_text = text[: op["start"]] + text[op["end"]:]
        elif otype == "replace":
            self._require_keys(op, ["start", "end", "text"], op_index)
            self._check_range(op, op_index, op["start"], op["end"])
            new_text = text[: op["start"]] + op["text"] + text[op["end"]:]
        else:
            raise EditorError(op_index, op, "未知操作类型: {!r}（支持 insert/delete/replace）".format(otype))

        new_version = self._current + 1
        self._snapshots[new_version] = new_text  # 回滚后的新分支会覆盖旧快照
        self.log.append((op_index, op, self._current, new_version))
        self._current = new_version
        return new_version

    # -- 回滚 ---------------------------------------------------------------

    def rollback_to(self, version):
        """回滚到任意已存在的历史版本。"""
        if version not in self._snapshots:
            raise EditorError(
                "-", {"rollback_to": version},
                "回滚失败：版本 v{} 不存在（已有版本: {}）".format(version, sorted(self._snapshots)),
            )
        self._current = version
        return self.text

    def reset(self):
        """整体回到初始状态（原子性失败时使用）。"""
        self._snapshots = {0: self._initial_text}
        self._current = 0
        self.log.clear()

    # -- 原子批量应用 --------------------------------------------------------

    def apply_sequence(self, operations):
        """原子地按顺序应用一批操作。

        全部成功 -> 返回每步产生的新版本号列表；
        任意一步失败 -> 整体回滚到初始状态（版本 0、初始文本），并抛出该错误。
        """
        applied_versions = []
        for i, op in enumerate(operations):
            try:
                applied_versions.append(self.apply(op, op_index=i))
            except EditorError:
                self.reset()  # 原子性：不允许半途而废
                raise
        return applied_versions


# ---------------------------------------------------------------------------
# JSON 驱动入口
# ---------------------------------------------------------------------------

def run_scenario(spec):
    """执行一份 JSON 场景描述，返回可打印的报告字符串。"""
    kernel = EditorKernel(spec.get("initial_text", ""))
    lines = []
    lines.append("初始文本 (v0): {!r}".format(kernel.text))

    def apply_batch(ops, label):
        if not ops:
            return True
        lines.append("-- 应用{}（共 {} 个操作）--".format(label, len(ops)))
        try:
            versions = kernel.apply_sequence(ops)
        except EditorError as exc:
            lines.append("✗ {}".format(exc))
            lines.append("  => 原子性生效：已整体回滚到初始状态 v{}，文本 = {!r}".format(kernel.version, kernel.text))
            return False
        for (idx, op, frm, to) in kernel.log[-len(ops):]:
            lines.append(
                "  ✓ #{} {:<8} v{} -> v{}: {!r}".format(idx, op["type"], frm, to, kernel.version_text(to))
            )
        return True

    ok = apply_batch(spec.get("operations", []), "操作序列")

    if ok and "rollback_to" in spec:
        target = spec["rollback_to"]
        lines.append("-- 回滚到 v{} --".format(target))
        try:
            kernel.rollback_to(target)
        except EditorError as exc:
            lines.append("✗ {}".format(exc))
        else:
            lines.append("  回滚后文本 (v{}): {!r}".format(kernel.version, kernel.text))
        apply_batch(spec.get("operations_after_rollback", []), "回滚后的新操作")

    lines.append("最终文本 (v{}): {!r}".format(kernel.version, kernel.text))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 内置演示
# ---------------------------------------------------------------------------

DEMO_FINAL_TEXT = {
    "initial_text": "hello world",
    "operations": [
        {"type": "insert",  "base": 0, "pos": 5, "text": ","},
        {"type": "replace", "base": 1, "start": 7, "end": 12, "text": "Python"},
        {"type": "delete",  "base": 2, "start": 5, "end": 6},
        {"type": "insert",  "base": 3, "pos": 12, "text": "!"},
    ],
}

DEMO_ROLLBACK = {
    "initial_text": "abcdef",
    "operations": [
        {"type": "insert", "base": 0, "pos": 3, "text": "XX"},
        {"type": "delete", "base": 1, "start": 0, "end": 2},
        {"type": "replace", "base": 2, "start": 3, "end": 5, "text": "ZZ"},
    ],
    "rollback_to": 1,
    "operations_after_rollback": [
        {"type": "replace", "base": 1, "start": 0, "end": 3, "text": "Q"},
        {"type": "insert",  "base": 2, "pos": 6, "text": "<end>"},
    ],
}

DEMO_CONFLICT = {
    "initial_text": "version control",
    "operations": [
        {"type": "insert",  "base": 0, "pos": 0, "text": "[v1] "},
        {"type": "insert",  "base": 1, "pos": 5, "text": "[v2] "},
        # 下面这个操作基于 v1，但此时最新已是 v2（中间隔着操作 #1）=> 冲突
        {"type": "delete",  "base": 1, "start": 0, "end": 5},
        {"type": "insert",  "base": 3, "pos": 0, "text": "never"},
    ],
}

DEMO_OUT_OF_RANGE = {
    "initial_text": "short",
    "operations": [
        {"type": "insert", "base": 0, "pos": 5, "text": " text"},
        # 文本长度只有 10，删除区间 [8, 99) 越界
        {"type": "delete", "base": 1, "start": 8, "end": 99},
        {"type": "insert", "base": 2, "pos": 0, "text": "never"},
    ],
}


def run_demo():
    sections = [
        ("示例 1：顺序应用全部操作 -> 最终文本", DEMO_FINAL_TEXT),
        ("示例 2：回滚到历史版本后继续应用新操作", DEMO_ROLLBACK),
        ("示例 3：版本冲突定位（base 不是最新版本，且整体回滚保证原子性）", DEMO_CONFLICT),
        ("示例 4：操作位置越界定位（并整体回滚）", DEMO_OUT_OF_RANGE),
    ]
    out = []
    for title, spec in sections:
        out.append("=" * 68)
        out.append(title)
        out.append("=" * 68)
        out.append(run_scenario(spec))
        out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv):
    if len(argv) == 1:
        print(run_demo())
        return 0
    if argv[1] == "-":
        spec = json.load(sys.stdin)
    else:
        with open(argv[1], "r", encoding="utf-8") as fh:
            spec = json.load(fh)
    print(run_scenario(spec))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
