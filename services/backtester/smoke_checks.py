"""Static smoke checks untuk kontrak modul backtester hasil refactor.

Jalankan manual:
  python3 -m services.backtester.smoke_checks

Check ini tidak meng-import modul backtester agar tidak tergantung dependency DB host.
"""

import ast
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


def _read_module_tree(module_filename: str) -> ast.Module:
    src = (BASE_DIR / module_filename).read_text(encoding="utf-8")
    return ast.parse(src, filename=module_filename)


def _get_function_argc(tree: ast.Module, fn_name: str) -> int:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == fn_name:
            return len(node.args.args)
    raise AssertionError(f"Function not found: {fn_name}")


def _get_constant_value(tree: ast.Module, const_name: str):
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == const_name:
                    if isinstance(node.value, ast.Constant):
                        return node.value.value
                    raise AssertionError(f"Constant {const_name} is not literal")
    raise AssertionError(f"Constant not found: {const_name}")


def run() -> None:
    runtime_ast = _read_module_tree("runtime.py")
    core_ast = _read_module_tree("core.py")
    pipeline_ast = _read_module_tree("pipeline.py")
    advisor_ast = _read_module_tree("advisor.py")
    reporting_ast = _read_module_tree("reporting.py")

    if _get_constant_value(runtime_ast, "OHLCV_DAYS") != 90:
        raise AssertionError("OHLCV_DAYS should remain 90 by default")

    if _get_function_argc(core_ast, "_run_backtest_pair") < 5:
        raise AssertionError("core._run_backtest_pair contract changed")
    if _get_function_argc(core_ast, "_get_signal_history") < 3:
        raise AssertionError("core._get_signal_history contract changed")
    if _get_function_argc(core_ast, "_process_pair_worker") < 4:
        raise AssertionError("core._process_pair_worker contract changed")

    if _get_function_argc(pipeline_ast, "run_backtest") < 0:
        raise AssertionError("pipeline.run_backtest contract changed")
    if _get_function_argc(pipeline_ast, "_persist_results") < 4:
        raise AssertionError("pipeline._persist_results contract changed")

    if _get_function_argc(advisor_ast, "tune_profile") < 1:
        raise AssertionError("advisor.tune_profile contract changed")
    if _get_function_argc(advisor_ast, "analyze_ohlcv_target_setup") < 0:
        raise AssertionError("advisor.analyze_ohlcv_target_setup contract changed")

    if _get_function_argc(reporting_ast, "_print_run_report") < 2:
        raise AssertionError("reporting._print_run_report contract changed")
    if _get_function_argc(reporting_ast, "_print_comparison_table") < 0:
        raise AssertionError("reporting._print_comparison_table contract changed")

    print("OK: backtester smoke checks passed")


if __name__ == "__main__":
    run()
