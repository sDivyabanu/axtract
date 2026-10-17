"""Crude static check: names that are loaded but never bound (assigned/imported/defined/argument) in a file.

Catches the 'forgot to import X' class of merge defect without extra dependencies. Not a full linter.
usage: check_undefined_names.py FILE [FILE ...]   (exit 1 if anything is found)
"""
import ast
import builtins
import sys

problems = 0
for path in sys.argv[1:]:
    tree = ast.parse(open(path, encoding="utf-8-sig").read(), path)
    bound = set(dir(builtins)) | {"__file__", "__name__", "__doc__"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                bound.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
            if not isinstance(node, ast.ClassDef):
                args = node.args
                for a in args.posonlyargs + args.args + args.kwonlyargs + ([args.vararg] if args.vararg else []) + ([args.kwarg] if args.kwarg else []):
                    bound.add(a.arg)
        elif isinstance(node, ast.Lambda):
            for a in node.args.args + node.args.kwonlyargs:
                bound.add(a.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in bound:
            print(f"{path}:{node.lineno}: undefined name {node.id!r}")
            problems += 1
sys.exit(1 if problems else 0)
