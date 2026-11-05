"""Trusted ordinary-behavior grader, copied outside the candidate workspace.

Run only inside the Sandbox. The compact output fits the acceptance pipeline's
bounded stdout. Candidate prints are suppressed; this is not an adversarial judge.
"""

from __future__ import annotations

import contextlib
import copy
import importlib.util
import json
import os
import sys


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def main() -> None:
    module_path, function_name, cases_path, marker = sys.argv[1:]
    with open(cases_path, encoding="utf-8") as stream:
        cases = json.load(stream)
    codes, details = [], {}
    with open(os.devnull, "w") as sink, contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        try:
            spec = importlib.util.spec_from_file_location("submission", module_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            function = getattr(module, function_name)
            for index, case in enumerate(cases):
                arguments = copy.deepcopy(case["args"])
                before = canonical(arguments)
                try:
                    actual = function(*arguments)
                    encoded = canonical(actual)
                    mutated = canonical(arguments) != before
                    code = 2 if mutated else 1 if encoded != canonical(case["expected"]) else 0
                    if code and len(details) < 3:
                        details[str(index)] = {"actual": encoded[:96]}
                except BaseException as exc:
                    code = 3
                    if len(details) < 3:
                        details[str(index)] = {"exception_type": type(exc).__name__[:40]}
                codes.append(code)
        except BaseException as exc:
            codes = [4] * len(cases)
            details = {"0": {"exception_type": type(exc).__name__[:40]}}
    output = {"codes": codes, "details": details}
    if len(canonical(output)) > 1400:
        output["details"] = {}
        output["details_omitted"] = True
    print(marker + canonical(output), flush=True)
    raise SystemExit(0 if all(code == 0 for code in codes) else 1)


if __name__ == "__main__":
    main()
