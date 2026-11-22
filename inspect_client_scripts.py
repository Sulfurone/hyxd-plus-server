"""Inspect code metadata from installed client scripts without executing them."""

import argparse
from io import BytesIO
import json
from pathlib import Path

from xdis.unmarshal import load_code

from pgx_resources import decode_record, record_offsets


def code_tree(code):
    yield code
    for value in code.co_consts:
        if hasattr(value, "co_code"):
            yield from code_tree(value)


def inspect(archive, filenames, functions):
    for offset in record_offsets(archive):
        try:
            payload, end = decode_record(archive, offset)
        except ValueError:
            continue
        if not payload.startswith(b"c"):
            continue
        if filenames and not any(name.encode() in payload for name in filenames):
            continue
        code = load_code(BytesIO(payload), 62211, bytes_for_s=True)
        if filenames and not any(name in code.co_filename for name in filenames):
            continue
        entries = []
        for function in code_tree(code):
            if functions and not any(name.lower() in function.co_name.lower() for name in functions):
                continue
            constants = []
            for value in function.co_consts:
                if hasattr(value, "co_code"):
                    constants.append({"function": value.co_name})
                elif isinstance(value, bytes):
                    constants.append(value.decode("utf-8", "backslashreplace"))
                elif isinstance(value, (str, int, float, bool, type(None))):
                    constants.append(value)
                else:
                    constants.append(repr(value))
            entries.append({"name": function.co_name, "names": function.co_names,
                            "varnames": function.co_varnames, "constants": constants,
                            "bytecode": function.co_code.hex()})
        yield {"offset": hex(offset), "file": code.co_filename, "functions": entries}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", action="append", default=[])
    parser.add_argument("--function", action="append", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    archive = Path(__file__).resolve().parent.parent / "res/entities.pg"
    result = list(inspect(archive.read_bytes(), args.file, args.function))
    text = json.dumps(result, indent=2, ensure_ascii=True)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text)
