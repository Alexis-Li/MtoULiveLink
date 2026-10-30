# Copyright (C) 2026 Alexis-Li
# SPDX-License-Identifier: GPL-3.0-only
# Licensed under the GPL with additional terms; see NOTICE.
"""The Appropriate Legal Notices the Maya panel displays."""

from .config import __version__

LEGAL_TITLE = "关于与许可"
LEGAL_TEXT = (
    "MtoU Live Link {0}\n"
    "版权所有 (C) 2026 Alexis-Li\n"
    "https://github.com/Alexis-Li/MtoULiveLink\n\n"
    "本程序是自由软件，你可以按 GNU 通用公共许可证第 3 版以及 NOTICE 中的附加条款"
    "再分发和修改。\n"
    "本程序不提供任何担保。\n\n"
    "查看许可证：发行包中的 LICENSE 和 NOTICE，或 "
    "https://www.gnu.org/licenses/gpl-3.0.html"
).format(__version__)
