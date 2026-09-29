"""Global opencode.json'a telegram MCP kaydini idempotent ekler (backup cagiran tarafta).

Kullanim: python merge_global_config.py <config.json> <server.py> <telegram-ops.md> [python_bin]
"""
import json
import sys


def main():
    cfg_path, server_py, steer = sys.argv[1:4]
    py_bin = sys.argv[4] if len(sys.argv) > 4 else "python"
    with open(cfg_path, encoding="utf-8-sig") as f:
        raw = f.read().strip()
    cfg = json.loads(raw) if raw else {}
    cfg.setdefault("mcp", {})["telegram"] = {
        "type": "local",
        "command": [py_bin, server_py],
        "enabled": True,
    }
    instr = cfg.get("instructions", [])
    if steer not in instr:
        cfg["instructions"] = instr + [steer]
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    print("OK: %s guncellendi" % cfg_path)


if __name__ == "__main__":
    main()
