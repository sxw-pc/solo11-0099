"""Minimal pytest shim (stdlib only) for offline environments without pytest.

Supports the subset used by this repository: ``pytest.raises`` as a context
manager with ``match``, plain ``test_*`` functions and a ``tmp_path`` fixture.
"""
import importlib.util
import inspect
import re
import sys
import tempfile
from pathlib import Path


class _Raises:
    def __init__(self, exc, match=None):
        self._exc = exc
        self._pattern = match
        self.value = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc is None:
            raise AssertionError(f'{self._exc.__name__} was not raised')
        if not issubclass(exc_type, self._exc):
            return False
        if self._pattern is not None and not re.search(self._pattern, str(exc)):
            raise AssertionError(
                f'pattern {self._pattern!r} not found in {str(exc)!r}')
        self.value = exc
        return True


def raises(exc, match=None):
    return _Raises(exc, match)


def main(test_paths):
    failures = []
    passed = 0
    for idx, test_path in enumerate(test_paths):
        mod_name = f'_pytest_shim_{idx}_{test_path.stem}'
        spec = importlib.util.spec_from_file_location(mod_name, test_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if not name.startswith('test_') or fn.__module__ != mod_name:
                continue
            kwargs = {}
            if 'tmp_path' in inspect.signature(fn).parameters:
                kwargs['tmp_path'] = Path(tempfile.mkdtemp(prefix='sitewalk-test-'))
            try:
                fn(**kwargs)
                passed += 1
                print(f'PASS {test_path.name}::{name}')
            except Exception as exc:  # noqa: BLE001 - test runner reports all
                failures.append((test_path.name, name, exc))
                print(f'FAIL {test_path.name}::{name}: {exc!r}')
    print(f'\n{passed} passed, {len(failures)} failed')
    if failures:
        for file, name, exc in failures:
            print(f'--- {file}::{name}')
            raise exc
    return 0


if __name__ == '__main__':
    import importlib.util  # noqa: E402
    sys.exit(main([Path(p) for p in sys.argv[1:]]))
