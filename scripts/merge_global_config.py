"""Global opencode.json'daki telegram MCP kaydini ekler veya kaldirir.

Kullanim:
    python merge_global_config.py <config.json> <server.py> <telegram-ops.md> [python_bin]
    python merge_global_config.py <config.json> --remove <telegram-ops.md>

Idempotent: ayni komut iki kez calistirilirsa sonuc degismez. Cagiran taraf
(setup.bat / install-global.*) yazmadan once yedek alir. Kaldirma modunda
telegram MCP kaydi ve steering talimati yapilandirmadan silinir; kullaniciya
ait diger MCP'ler ve talimatlar korunur.
"""
import json
import sys


def main():
    cfg_path, arg2, steer = sys.argv[1:4]
    removing = arg2 == "--remove"
    py_bin = (sys.argv[4] if len(sys.argv) > 4 else "python") if not removing else None
    server_py = None if removing else arg2
    with open(cfg_path, encoding="utf-8-sig") as f:
        raw = f.read().strip()
    cfg = json.loads(raw) if raw else {}

    if removing:
        mcp = cfg.get("mcp")
        if isinstance(mcp, dict):
            mcp.pop("telegram", None)
            if not mcp:
                cfg.pop("mcp", None)
        instr = cfg.get("instructions")
        if isinstance(instr, list):
            cfg["instructions"] = [i for i in instr if i != steer]
            if not cfg["instructions"]:
                cfg.pop("instructions", None)
        action = "kaldirildi"
    else:
        cfg.setdefault("mcp", {})["telegram"] = {
            "type": "local",
            "command": [py_bin, server_py],
            "enabled": True,
        }
        instr = cfg.get("instructions", [])
        if steer not in instr:
            cfg["instructions"] = instr + [steer]
        action = "guncellendi"

    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    print("OK: %s %s" % (cfg_path, action))


if __name__ == "__main__":
    main()
