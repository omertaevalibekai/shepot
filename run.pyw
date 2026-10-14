"""Запуск без окна консоли (используется автозапуском и ярлыком)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

if __name__ == "__main__" and "--ghost-helper" in sys.argv:
    # помощник окна-призрака: тот же exe, но без Qt и без проверки «уже запущен»
    from shepot.ghost_hook import helper_main  # noqa: E402

    sys.exit(helper_main(sys.argv))

from shepot.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
