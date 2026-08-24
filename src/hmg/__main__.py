import sys

if len(sys.argv) > 1 and sys.argv[1] == "--elevated-helper":
    from hmg.privileged_helper import main as helper_main

    raise SystemExit(helper_main(sys.argv[2:]))

from hmg.ui import main

raise SystemExit(main())
