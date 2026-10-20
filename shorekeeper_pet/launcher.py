"""Keep startup diagnostics available even when the packaged UI cannot load."""
import pathlib,sys,traceback
from .paths import ROOT

def run():
    try:
        if '--shorekeeper-event' in sys.argv or '--shorekeeper-status' in sys.argv:
            from .telemetry import main
            return main('--shorekeeper-status' in sys.argv)
        if '--watch-claude' in sys.argv:
            from .startup import watch
            return watch()
        from .pet import main
        return main()
    except Exception:
        (ROOT/'startup-error.log').write_text(traceback.format_exc(),encoding='utf8')
        if '--verify-package' not in sys.argv:
            raise
        return 1

if __name__=='__main__':
    sys.exit(run())
