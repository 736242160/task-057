#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""editor_kernel.py — 版本化编辑器内核（纯 Python 标准库，单文件）

功能：
  - 编辑操作：insert / delete / replace，每个操作携带 base_version（基于哪个版本）
  - 版本一致性：操作的 base_version 不是当前最新版本时报冲突（定位到具体操作）
  - 回滚：可回滚到任意历史版本，回滚后继续应用新操作，版本号继续递增
  - 越界检测：操作位置超出文本长度时报告是哪个操作
  - 原子性：命令序列要么全部按顺序应用，要么整体回滚到初始状态

用法：
  python3 editor_kernel.py --demo            # 运行内置样例（最终文本/回滚/冲突/越界）
  python3 editor_kernel.py input.json        # 从 JSON 文件读取命令序列
  cat input.json | python3 editor_kernel.py  # 从标准输入读取

输入 JSON 格式：
{
  "initial_text": "hello world",
  "commands": [
    {"op": "insert",  "id": "op1", "base_version": 0, "position": 5, "content": ","},
    {"op": "delete",  "id": "op2", "base_version": 1, "start": 0, "end": 5},
    {"op": "replace", "id": "op3", "base_version": 2, "start": 0, "end": 3, "content": "HEY"},
    {"op": "rollback", "to_version": 1}
  ]
}
"""

import json
import sys
from dataclasses import dataclass


# ---------------------------------------------------------------- 异常类型

class EditorError(Exception):
    """编辑器内核错误基类。"""


class ConflictError(EditorError):
    """操作的 base_version 与当前最新版本不一致。"""

    def __init__(self, op_id, base_version, current_version):
        self.op_id = op_id
        self.base_version = base_version
        self.current_version = current_version
        super().__init__(
            "冲突：操作 '%s' 基于版本 v%d，但当前最新版本是 v%d"
            "（中间存在其他操作）" % (op_id, base_version, current_version)
        )


class OutOfBoundsError(EditorError):
    """操作位置超出文本长度。"""

    def __init__(self, op_id, detail):
        self.op_id = op_id
        super().__init__("越界：操作 '%s' %s" % (op_id, detail))


class UnknownVersionError(EditorError):
    """回滚目标版本不存在。"""

    def __init__(self, to_version, current_version):
        super().__init__(
            "回滚失败：目标版本 v%d 不存在（当前历史范围 v0..v%d）"
            % (to_version, current_version)
        )


# ---------------------------------------------------------------- 内核

@dataclass
class Version:
    number: int       # 版本号（单调递增）
    text: str         # 该版本的文本快照
    caused_by: str    # 产生该版本的操作 id（initial / rollback）


class EditorKernel:
    """版本化编辑器内核。

    history 是线性版本链：history[-1] 即当前最新版本。
    回滚会截断目标版本之后的“未来”版本，之后的新操作从目标版本继续编号。
    """

    def __init__(self, initial_text):
        self._initial_text = initial_text
        self.history = [Version(0, initial_text, "initial")]

    # -- 状态查询 ------------------------------------------------------

    @property
    def current(self):
        return self.history[-1]

    @property
    def text(self):
        return self.current.text

    # -- 位置校验 ------------------------------------------------------

    @staticmethod
    def _check_insert_pos(op_id, position, text):
        if not isinstance(position, int) or not 0 <= position <= len(text):
            raise OutOfBoundsError(
                op_id,
                "插入位置 %r 超出范围 [0, %d]（文本长度 %d）"
                % (position, len(text), len(text)),
            )

    @staticmethod
    def _check_range(op_id, start, end, text):
        ok = (
            isinstance(start, int)
            and isinstance(end, int)
            and 0 <= start <= end <= len(text)
        )
        if not ok:
            raise OutOfBoundsError(
                op_id,
                "区间 [%r, %r) 超出范围 [0, %d]（文本长度 %d）"
                % (start, end, len(text), len(text)),
            )

    # -- 编辑操作 ------------------------------------------------------

    def apply(self, cmd):
        """应用单个编辑操作，返回新版本。失败抛 EditorError，自身状态不变。"""
        op_type = cmd.get("op")
        op_id = cmd.get("id", "<未命名操作>")
        base = cmd.get("base_version")

        # 版本一致性检查：操作必须基于当前最新版本
        if base != self.current.number:
            raise ConflictError(op_id, base, self.current.number)

        old = self.current.text

        if op_type == "insert":
            pos = cmd.get("position")
            content = cmd.get("content", "")
            self._check_insert_pos(op_id, pos, old)
            new_text = old[:pos] + content + old[pos:]

        elif op_type == "delete":
            start, end = cmd.get("start"), cmd.get("end")
            self._check_range(op_id, start, end, old)
            new_text = old[:start] + old[end:]

        elif op_type == "replace":
            start, end = cmd.get("start"), cmd.get("end")
            content = cmd.get("content", "")
            self._check_range(op_id, start, end, old)
            new_text = old[:start] + content + old[end:]

        else:
            raise EditorError("操作 '%s' 类型未知：%r" % (op_id, op_type))

        version = Version(self.current.number + 1, new_text, op_id)
        self.history.append(version)
        return version

    # -- 回滚 ----------------------------------------------------------

    def rollback(self, to_version):
        """回滚到任意历史版本，截断其后的版本。返回回滚后的当前版本。"""
        if not isinstance(to_version, int) or not 0 <= to_version <= self.current.number:
            raise UnknownVersionError(to_version, self.current.number)
        del self.history[to_version + 1:]
        return self.current

    def reset(self):
        """整体回滚到初始状态（原子性保证用）。"""
        del self.history[1:]


# ---------------------------------------------------------------- 命令序列执行（原子）

def run_commands(initial_text, commands):
    """按顺序执行命令序列，保证原子性：

    任一命令失败（冲突 / 越界 / 非法回滚）时，整体回滚到初始状态。
    返回报告 dict（可 JSON 序列化）。
    """
    kernel = EditorKernel(initial_text)
    log = []
    success = True
    error = None

    for index, cmd in enumerate(commands):
        label = cmd.get("id") or ("命令#%d" % index)
        try:
            if cmd.get("op") == "rollback":
                version = kernel.rollback(cmd.get("to_version"))
                log.append({
                    "command": label,
                    "action": "rollback",
                    "to_version": version.number,
                    "text": version.text,
                    "status": "ok",
                })
            else:
                version = kernel.apply(cmd)
                log.append({
                    "command": label,
                    "action": cmd.get("op"),
                    "new_version": version.number,
                    "text": version.text,
                    "status": "ok",
                })
        except EditorError as exc:
            success = False
            error = {"command": label, "message": str(exc)}
            log.append({"command": label, "status": "failed", "message": str(exc)})
            kernel.reset()
            log.append({
                "command": "<atomic>",
                "status": "rolled_back",
                "message": "命令序列失败，已整体回滚到初始状态 v0",
            })
            break

    return {
        "success": success,
        "error": error,
        "final_version": kernel.current.number,
        "final_text": kernel.text,
        "log": log,
        "history": [
            {"version": v.number, "caused_by": v.caused_by, "text": v.text}
            for v in kernel.history
        ],
    }


# ---------------------------------------------------------------- 内置样例

def _print_report(title, report):
    print("=" * 64)
    print(title)
    print("=" * 64)
    for entry in report["log"]:
        if entry["status"] == "ok" and entry.get("action") == "rollback":
            print("  [回滚] %s -> v%d  文本: %r"
                  % (entry["command"], entry["to_version"], entry["text"]))
        elif entry["status"] == "ok":
            print("  [应用] %s (%s) -> v%d  文本: %r"
                  % (entry["command"], entry["action"],
                     entry["new_version"], entry["text"]))
        else:
            print("  [失败] %s: %s" % (entry["command"], entry["message"]))
    print("-" * 64)
    print("  成功: %s | 最终版本: v%d" % (report["success"], report["final_version"]))
    print("  最终文本: %r" % report["final_text"])
    print("  版本历史: " + " | ".join(
        "v%d(%s)" % (h["version"], h["caused_by"]) for h in report["history"]))
    print()


def run_demo():
    # 样例 1：正常流程，输出最终文本
    _print_report("样例 1：顺序应用 insert / delete / replace", run_commands(
        "hello world",
        [
            {"op": "insert", "id": "op1", "base_version": 0,
             "position": 5, "content": ","},
            {"op": "replace", "id": "op2", "base_version": 1,
             "start": 7, "end": 12, "content": "python"},
            {"op": "delete", "id": "op3", "base_version": 2,
             "start": 5, "end": 6},
        ],
    ))

    # 样例 2：回滚到历史版本后继续编辑
    _print_report("样例 2：回滚到 v1 后继续应用新操作", run_commands(
        "abcdef",
        [
            {"op": "insert", "id": "op1", "base_version": 0,
             "position": 6, "content": "XYZ"},
            {"op": "delete", "id": "op2", "base_version": 1,
             "start": 0, "end": 2},
            {"op": "rollback", "to_version": 1},
            {"op": "replace", "id": "op3", "base_version": 1,
             "start": 0, "end": 3, "content": "ABC"},
        ],
    ))

    # 样例 3：版本冲突定位（op3 基于 v1，但最新已是 v2）+ 原子回滚
    _print_report("样例 3：冲突定位与整体回滚", run_commands(
        "hello world",
        [
            {"op": "insert", "id": "op1", "base_version": 0,
             "position": 5, "content": ","},
            {"op": "insert", "id": "op2", "base_version": 1,
             "position": 0, "content": ">> "},
            {"op": "delete", "id": "op3", "base_version": 1,  # 过期版本号
             "start": 0, "end": 3},
        ],
    ))

    # 样例 4：位置越界，报告具体操作 + 原子回滚
    _print_report("样例 4：越界定位与整体回滚", run_commands(
        "short",
        [
            {"op": "insert", "id": "op1", "base_version": 0,
             "position": 0, "content": "a "},
            {"op": "delete", "id": "op2", "base_version": 1,
             "start": 3, "end": 99},
        ],
    ))


# ---------------------------------------------------------------- 入口

def main(argv):
    if len(argv) > 1 and argv[1] == "--demo":
        run_demo()
        return 0

    raw = open(argv[1], encoding="utf-8").read() if len(argv) > 1 else sys.stdin.read()
    payload = json.loads(raw)
    report = run_commands(payload.get("initial_text", ""),
                          payload.get("commands", []))
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if report["success"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
