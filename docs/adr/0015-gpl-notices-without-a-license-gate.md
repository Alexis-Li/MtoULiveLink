# GPL notices and section 7 terms, without a license gate

MtoU Live Link is licensed under GPL-3.0-only with an additional permission and
additional terms under section 7, all stated in `NOTICE`. Both release archives
carry `LICENSE` and `NOTICE`, shipped source files carry a copyright and SPDX
header that points to `NOTICE`, and both hosts display the Appropriate Legal
Notices: the **关于与许可** button in the Maya panel and the group of the same
name in the Binding Actor's Details.

The notices are displayed and never checked. Nothing refuses to start when they
are missing or changed. A check could be removed by editing one line of source
that the recipient holds, section 3 of the License gives it no legal force, and
a false positive would stop a production. Removing `legal.py` still produces the
existing incomplete-install message, only because the panel imports it.

The protection therefore applies to works that are conveyed: section 5(d) makes
a modified version keep the displayed notices, and section 7 covers attribution,
misrepresented origin, publicity and the product's names. Use inside a company
that conveys nothing is allowed by section 2, unattributed and modified included.

The additional permission covers linking or combining with Autodesk Maya and
Unreal Engine, whose licenses are not compatible with the GPL. It was written
without legal review and its effect against the Unreal Engine EULA is unconfirmed.

Outside code contributions are not accepted for now, so a single copyright holder
can still change the terms of later versions. Releases already published keep the
terms they shipped with.
