"""Reconstruct the login schema from the client's captured FileDescriptorProto."""
import json
from pathlib import Path

from gt_protocol import fields

ROOT = Path(__file__).resolve().parent / "protocol"


def values(data, number):
    return [value for _, value in fields(data).get(number, [])]


def first(data, number, default=""):
    items = values(data, number)
    value = items[0] if items else default
    return value.decode("utf-8") if isinstance(value, bytes) else value


def descriptor(data):
    return {
        "name": first(data, 1),
        "fields": [{key: first(value, number) for key, number in
                    [("name", 1), ("number", 3), ("label", 4), ("type", 5),
                     ("type_name", 6), ("default", 7)]}
                   for value in values(data, 2)],
        "nested": [descriptor(value) for value in values(data, 3)],
        "enums": [{"name": first(value, 1),
                   "values": [{"name": first(item, 1), "number": first(item, 2)}
                              for item in values(value, 2)]}
                  for value in values(data, 4)],
    }


TYPES = dict(enumerate([
    "double", "float", "int64", "uint64", "int32", "fixed64", "fixed32", "bool",
    "string", "group", "message", "bytes", "uint32", "enum", "sfixed32",
    "sfixed64", "sint32", "sint64"], 1))


def proto_message(item, depth=0):
    indent = "  " * depth
    result = [indent + "message " + item["name"] + " {"]
    for enum in item["enums"]:
        result.append(indent + "  enum " + enum["name"] + " {")
        for value in enum["values"]:
            number = value["number"]
            if number >= 1 << 63:
                number -= 1 << 64
            result.append(indent + "    %s = %d;" % (value["name"], number))
        result.append(indent + "  }")
    for nested in item["nested"]:
        result.extend(proto_message(nested, depth + 1))
    for field in item["fields"]:
        label = {1: "optional", 2: "required", 3: "repeated"}[field["label"]]
        kind = field["type_name"] or TYPES[field["type"]]
        # The binary descriptor remains authoritative for field defaults/options.
        result.append(indent + "  %s %s %s = %d;" %
                      (label, kind, field["name"], field["number"]))
    result.append(indent + "}")
    return result


def main():
    blob = (ROOT / "login.descriptor.bin").read_bytes()
    schema = {"name": first(blob, 1), "package": first(blob, 2),
              "dependencies": [value.decode() for value in values(blob, 3)],
              "messages": [descriptor(value) for value in values(blob, 4)],
              "services": []}
    for service in values(blob, 6):
        schema["services"].append({"name": first(service, 1), "methods": [
            {"index": index, "name": first(method, 1), "input": first(method, 2),
             "output": first(method, 3)}
            for index, method in enumerate(values(service, 2))]})
    (ROOT / "login.schema.json").write_text(json.dumps(schema, indent=2), encoding="utf-8")
    lines = ['// Reconstructed from the running original 3.0.43 client.',
             '// Defaults and file options are preserved in login.descriptor.bin.',
             'syntax = "proto2";', 'package ' + schema["package"] + ';',
             'import "common.proto";', '']
    for item in schema["messages"]:
        lines.extend(proto_message(item) + [""])
    for service in schema["services"]:
        lines.append("service " + service["name"] + " {")
        for method in service["methods"]:
            lines.append("  rpc %s (%s) returns (%s);" %
                         (method["name"], method["input"], method["output"]))
        lines.extend(["}", ""])
    (ROOT / "login.proto").write_text("\n".join(lines), encoding="utf-8")
    (ROOT / "common.proto").write_text(
        '// Minimal compatibility dependency; not a dump of the original common.proto.\n'
        'syntax = "proto2";\npackage gt;\nmessage Void {}\n', encoding="utf-8")
    print("Reconstructed %d login messages and %d services" %
          (len(schema["messages"]), len(schema["services"])))


if __name__ == "__main__":
    main()
