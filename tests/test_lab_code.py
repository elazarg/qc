"""Checks of the app's code as text, with the standard library only (no linter is installed).

- Nothing is defined twice in one place. A second method of the same name in a class silently replaces the
  first; that once took a request to the assistant out of the earlier desk without a sign.
- Nothing is imported and never used. An import is used when its name appears in the file, when another file
  of the app imports that name from this one (a name passed on), or when its line says so with `# noqa: F401`.

What this cannot see: a name used only inside a string (`getattr(module, "name")`), and a name rebound before it
is used. Both are rare enough here to be handled where they come up.
"""

import sys as _sys, pathlib as _pathlib; _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
import isolated  # noqa: E402,F401  (a data folder of its own; no way to the real unit, driver or Codex)
import ast  # noqa: E402
from functools import lru_cache  # noqa: E402
from pathlib import Path  # noqa: E402
import unittest  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
APP = REPO / "lab"
REPLACING = {"setter", "getter", "deleter", "register", "overload"}     # decorators of a second definition meant as one


def sources(folder):
    return [(path, path.read_text(encoding="utf-8")) for path in sorted(folder.rglob("*.py"))]


@lru_cache(maxsize=None)
def parsed(text, where):
    return ast.parse(text, where)


def dotted(path):
    name = ".".join(path.relative_to(REPO).with_suffix("").parts)
    return name[:-len(".__init__")] if name.endswith(".__init__") else name


def meant_to_replace(node):
    for decorator in getattr(node, "decorator_list", ()):
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if (isinstance(target, ast.Attribute) and target.attr in REPLACING) \
                or (isinstance(target, ast.Name) and target.id in REPLACING):
            return True
    return False


def defined_twice(text, where="<text>"):
    """(where, line, name, line of the first) for every function or class defined a second time in the same
    class or at the top of the same module."""
    tree = parsed(text, where)
    found = []
    for scope in [tree, *(node for node in ast.walk(tree) if isinstance(node, ast.ClassDef))]:
        seen = {}
        for node in scope.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if node.name in seen and not meant_to_replace(node):
                found.append((where, node.lineno, node.name, seen[node.name]))
            seen.setdefault(node.name, node.lineno)
    return found


def imported(tree):
    """(line, the name bound, the module it comes from or None) for every import in a file."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, (alias.asname or alias.name).split(".")[0], None
        elif isinstance(node, ast.ImportFrom) and node.module != "__future__":
            for alias in node.names:
                if alias.name != "*":
                    yield node.lineno, alias.asname or alias.name, node.module


def used(tree):
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    for node in ast.walk(tree):                                  # names listed in __all__ are passed on
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            names |= {item.value for item in getattr(node.value, "elts", ()) if isinstance(item, ast.Constant)}
    return names


def never_used(files):
    """(where, line, name) of every import in `files`, (path, text) pairs, whose name is not used."""
    files = list(files)
    taken = set()                                                # (module, name) imported from it somewhere
    for path, text in files:
        for _, name, module in imported(parsed(text, path.relative_to(REPO).as_posix())):
            if module:
                taken.add((module, name))
    found = []
    for path, text in files:
        tree, lines, module = parsed(text, path.relative_to(REPO).as_posix()), text.splitlines(), dotted(path)
        names = used(tree)
        for line, name, _ in imported(tree):
            if name in names or (module, name) in taken or "noqa: F401" in lines[line - 1] \
                    or ("noqa" in lines[line - 1] and "F401" in lines[line - 1]):
                continue
            found.append((path.relative_to(REPO).as_posix(), line, name))
    return found


class CodeTest(unittest.TestCase):
    def test_nothing_in_the_app_is_defined_twice_in_the_same_place(self):
        found = [item for path, text in sources(APP)
                 for item in defined_twice(text, path.relative_to(REPO).as_posix())]
        self.assertEqual(found, [])

    def test_nothing_in_the_app_is_imported_and_never_used(self):
        self.assertEqual(never_used(sources(APP)), [])

    def test_the_checks_catch_what_they_are_for(self):
        twice = ("class Desk:\n    def ask(self):\n        return 1\n\n    def draw(self):\n        pass\n\n"
                 "    def ask(self):\n        return 2\n\n"
                 "    @property\n    def side(self):\n        return 'a'\n\n"
                 "    @side.setter\n    def side(self, value):\n        pass\n\n"
                 "def helper():\n    pass\n\ndef helper():\n    pass\n")
        self.assertEqual([(line, name, first) for _, line, name, first in defined_twice(twice)],
                         [(22, "helper", 19), (8, "ask", 2)])
        one = APP / "made_up_one.py"
        two = APP / "made_up_two.py"
        files = [(one, "import os\nimport json\nfrom pathlib import Path\nfrom lab.sound import catalog  # noqa: F401\n"
                       "from lab.sound.chain import Row, Block\n\nprint(json.dumps(Block))\n"),
                 (two, "from lab.made_up_one import Row\n\nprint(Row)\n")]
        self.assertEqual(never_used(files), [("lab/made_up_one.py", 1, "os"), ("lab/made_up_one.py", 3, "Path")])


if __name__ == "__main__":
    unittest.main()
