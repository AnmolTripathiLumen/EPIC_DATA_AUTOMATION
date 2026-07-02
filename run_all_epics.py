import argparse
import importlib.util
import os
import sys


def load_module(script_path: str):
    spec = importlib.util.spec_from_file_location("local_jira_data_extraction_enhanced", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load local extraction script")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_epics_file(epics_file: str):
    with open(epics_file, "r", encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip() and not line.strip().startswith("#")]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Jira quality extraction for a list of epics")
    parser.add_argument("--epics-file", required=True, help="Text file with one epic key per line")
    parser.add_argument("--output-file", default="All_Epics_Quality_Report.xlsx", help="Combined output file name")
    args = parser.parse_args()

    local_script = os.path.join(os.path.dirname(__file__), "jira_data_extraction_enhanced.py")
    if not os.path.exists(local_script):
        raise FileNotFoundError(f"Local script not found: {local_script}")

    epics_file = os.path.abspath(args.epics_file)
    if not os.path.exists(epics_file):
        raise FileNotFoundError(f"Epics file not found: {epics_file}")

    module = load_module(local_script)
    epic_keys = read_epics_file(epics_file)
    if not epic_keys:
        raise ValueError(f"No epic keys found in {epics_file}")

    module.EPIC_KEYS = epic_keys
    module.OUTPUT_FILE_EXCEL = args.output_file

    print(f"Running all epics from: {epics_file}")
    print(f"Epic count: {len(epic_keys)}")
    print(f"Output file: {module.OUTPUT_FILE_EXCEL}")

    module.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
