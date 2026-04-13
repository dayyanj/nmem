"""Allow running hooks via: python -m nmem.hooks <hook_name>"""

import sys

from nmem.hooks.scripts import session_start, post_tool_use, session_end


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m nmem.hooks <session_start|post_tool_use|session_end>", file=sys.stderr)
        sys.exit(1)

    hook_name = sys.argv[1]
    hooks = {
        "session_start": session_start,
        "post_tool_use": post_tool_use,
        "session_end": session_end,
    }

    handler = hooks.get(hook_name)
    if not handler:
        print(f"Unknown hook: {hook_name}. Valid: {', '.join(hooks)}", file=sys.stderr)
        sys.exit(1)

    handler()


if __name__ == "__main__":
    main()
